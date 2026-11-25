"""Build a blank operator handoff from the verified minimum template and current pilot contract."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from src.gas_application.pilot_cli import template
from src.gas_application.pilot_contract import PilotGrant, PilotManifest

REPO = Path(__file__).resolve().parents[1]
FIELDS = "docs/templates/gas-pilot-02-fields.csv"
PILOT_02 = "80b0b263b165b7bc1cc2359374b5dbda97320ccb"
CORE = ("nodes", "pipes", "sensors", "observations")
VALIDATION = ("threshold_rules", "events", "truth_windows", "truth_events")


def rows(raw: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def field_counts(records: list[dict[str, str]]) -> dict:
    keys = {(row["数据表"], row["字段名称"]) for row in records}
    names = Counter(field for _, field in keys)
    return {
        "rows": len(records), "unique_table_fields": len(keys), "duplicate_keys": len(records) - len(keys),
        "unique_bare_names": len(names), "reused_bare_names": sum(count > 1 for count in names.values()),
    }


def field_revision(historical: list[dict[str, str]], current: list[dict[str, str]]) -> dict:
    historical_keys = {(row["数据表"], row["字段名称"]) for row in historical}
    strata = {level: [row for row in current if row["交付级别"] == level] for level in ("A", "B", "C")}
    flat = [row for level in ("A", "B", "C") for row in strata[level]]
    accepted = strata["A"] + strata["B"]
    runtime_keys = {(row["数据表"], row["字段名称"]) for row in accepted}
    before, after, runtime = map(field_counts, (historical, current, accepted))
    counts = {level: len(items) for level, items in strata.items()}
    if (before["rows"] != 99 or any(item["duplicate_keys"] for item in (before, after, runtime))
        or len(flat) != len(current) or counts != {"A": 49, "B": 51, "C": 9}
        or historical_keys - runtime_keys or runtime_keys - historical_keys != {("observations", "quality_reason")}):
        raise ValueError("field revision does not match the reviewed handoff")
    return {"pilot_02": before, "release_04": after, "runtime_fields": runtime, "classes": counts}


def blank_payload(source: Path, generated: Path) -> dict[str, bytes]:
    with ZipFile(source) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate names in minimum template")
        manifest_bytes = archive.read("data/manifest.json")
        manifest = PilotManifest.model_validate_json(manifest_bytes)
        if manifest.data_mode != "synthetic" or set(manifest.files) != set(CORE):
            raise ValueError("minimum template needs four blank synthetic tables")
        payload = {"A-首批必需/manifest.json": manifest_bytes}
        for table in CORE:
            spec = manifest.files[table]
            if spec.filename != table + ".csv":
                raise ValueError("unexpected minimum template filename")
            data = archive.read("data/" + spec.filename)
            # Match the current generated header exactly. This also excludes any populated rows.
            if data != (generated / spec.filename).read_bytes() or spec.sha256 != sha256(data).hexdigest():
                raise ValueError("minimum template includes invalid headers, data rows, or digests")
            payload["A-首批必需/" + spec.filename] = data
    return payload


def authorization_form(current: list[dict[str, str]]) -> bytes:
    fields = {row["字段名称"]: row for row in current if row["数据表"] == "受控授权登记"}
    if set(fields) != set(PilotGrant.model_fields):
        raise ValueError("authorization form differs from the runtime contract")
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["确认项目", "运营方填写", "格式与核对要求", "来源或批准记录"])
    for name, row in fields.items():
        writer.writerow([name, "", row["类型"] + " · " + row["质量要求"], ""])
    for name, instruction in (
        ("数据权属单位", "登记数据权属单位及有权确认的岗位"),
        ("审批状态与签核", "填写签批人、签批时间、状态及盖章或审批单凭证引用"),
        ("脱敏范围与受控移交途径", "确认接收位置、脱敏规则、谁可查看及对外使用边界"),
        ("保存、撤销及清退", "确认留存期限、撤销联络方式和按批准规则清退的责任人"),
    ):
        writer.writerow([name, "", instruction, ""])
    return out.getvalue().encode("utf-8-sig")


def package(destination: Path, minimum: Path) -> dict:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    raw_fields = (REPO / FIELDS).read_bytes()
    old = subprocess.check_output(["git", "-C", str(REPO), "show", f"{PILOT_02}:{FIELDS}"])
    current = rows(raw_fields)
    audit = field_revision(rows(old), current)
    with tempfile.TemporaryDirectory(prefix="wpn-pilot-handoff-") as temp:
        root = Path(temp)
        template(root / "minimum", "syn-gas-pilot-minimum", "intake-v1", "synthetic", minimum=True)
        template(root / "full", "syn-gas-pilot-minimum", "intake-v1", "synthetic")
        files = blank_payload(minimum, root / "minimum")
        for table in VALIDATION:
            files[f"B-运营方签批与核验/{table}.csv"] = (root / "full" / f"{table}.csv").read_bytes()
    files["数据授权与使用范围确认表.csv"] = authorization_form(current)
    files["字段口径与填报清单.csv"] = raw_fields
    files["导入和验收指南.md"] = (REPO / "docs/development/GAS-PILOT-04-HANDOFF.md").read_bytes()
    files["字段统计.json"] = (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode()
    with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    return audit


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("destination", type=Path)
    cli.add_argument("--minimum", type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(package(args.destination, args.minimum), ensure_ascii=False))
