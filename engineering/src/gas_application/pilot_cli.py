"""Offline preview, import and template generation for the gas pilot."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path

from .data_package import IntakeError
from .pilot_contract import ROW_MODELS, PilotManifest
from .pilot_intake import CORE, inspect_pilot
from .pilot_store import PilotStore


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def template(path: Path, dataset_id: str, version: str, mode: str, *, minimum: bool = False) -> None:
    base = PilotManifest.model_validate({
        "schema_version": "gas-pilot/1", "dataset_id": dataset_id, "version": version, "data_mode": mode, "files": {},
    })
    path.mkdir(mode=0o700)
    manifest = base.model_dump(exclude_none=True)
    for table, (model, _) in ROW_MODELS.items():
        if table == "thresholds" or (minimum and table not in CORE):
            continue
        name = table + ".csv"
        keys = ["source_record_id"]
        for key, field in model.model_fields.items():
            if key != "source" and (not minimum or field.is_required() or key == "region_ref"):
                keys.append(key)
        with (path / name).open("x", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(keys)
        manifest["files"][table] = {
            "filename": name, "source_id": table, "version": version,
            "sha256": sha256((path / name).read_bytes()).hexdigest(), "columns": {key: key for key in keys},
        }
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "ingest", "reimport"):
        sub = commands.add_parser(name)
        sub.add_argument("source", type=Path)
        sub.add_argument("--store", type=Path, required=name != "preview")
        if name != "preview":
            sub.add_argument("--actor", help="operator identity from the approved admission registry")
    for name in ("quarantine", "rollback"):
        sub = commands.add_parser(name)
        sub.add_argument("--store", type=Path, required=True)
        sub.add_argument("--dataset", required=True)
        sub.add_argument("--version", required=True)
        sub.add_argument("--actor", required=True)
        sub.add_argument("--reason", required=True)
    sub = commands.add_parser("template")
    sub.add_argument("output", type=Path)
    sub.add_argument("--dataset", required=True)
    sub.add_argument("--version", required=True)
    sub.add_argument("--mode", choices=("synthetic", "real_authorized"), required=True)
    sub.add_argument("--profile", choices=("full", "minimum"), default="full")

    cfg = parser.parse_args()
    try:
        if cfg.command == "template":
            template(cfg.output, cfg.dataset, cfg.version, cfg.mode, minimum=cfg.profile == "minimum")
            _print({"status": "template_created"})
            return 0
        store = PilotStore(cfg.store) if cfg.store else None
        if cfg.command in ("quarantine", "rollback"):
            _print(store.change_version(cfg.dataset, cfg.version, action=cfg.command, actor=cfg.actor, reason=cfg.reason))
            return 0
        inspection = inspect_pilot(cfg.source, grants=store.grants() if store else ())
        report = inspection.report
        if cfg.command in ("ingest", "reimport") and store:
            report = {**report, "intake": store.prepare(inspection, actor=cfg.actor, readmit=cfg.command == "reimport")}
        _print(report)
        return 0 if inspection.snapshot is not None else 2
    except IntakeError as error:
        _print({"status": "rejected", "code": error.code, "table": error.table, "row": error.row})
        return 2
    except (OSError, ValueError):
        _print({"status": "rejected", "code": "invalid_template_or_store"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
