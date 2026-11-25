"""Run the supplied model's complete main function and compare its saved outputs.

Only Desktop path resolution and the evaluation clock are adapted.
The supplied source, input data, weights, and scoring functions stay unchanged.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import statistics
import sys
import time
import traceback
from types import ModuleType
import uuid
from zipfile import ZipFile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "02_核查证据/程序包/运行程序结果260918"
SOURCE = PACKAGE / "V1.0管道健康评价模型.py"
INPUTS = {
    "input_pipe_data.xlsx": PACKAGE / "输入/input_pipe_data.xlsx",
    "input_pipe_data_动态.xlsx": PACKAGE / "输入/input_pipe_data_动态.xlsx",
}
REFERENCE_XLSX = PACKAGE / "输出/V1.0健康评价结果.xlsx"
REFERENCE_TXT = PACKAGE / "输出/V1.0分析报告.txt"
ARCHIVE = ROOT / "01_原始材料/运行程序结果260918.zip"
CHINA = ZoneInfo("Asia/Shanghai")
TOLERANCE = 1e-9

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def relative(path):
    return path.relative_to(ROOT).as_posix()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def zip_name(info):
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name.replace("\\", "/")

def verify_supplied_copies():
    paths = [SOURCE, *INPUTS.values(), REFERENCE_XLSX, REFERENCE_TXT]
    with ZipFile(ARCHIVE) as archive:
        members = {zip_name(info): info for info in archive.infolist()}
        for path in paths:
            member_name = "运行程序结果260918/" + path.relative_to(PACKAGE).as_posix()
            original_hash = hashlib.sha256(archive.read(members[member_name])).hexdigest()
            if digest(path) != original_hash:
                raise RuntimeError("Extracted file differs from M07: " + relative(path))
    return True

def table_rows(workbook, name):
    sheet = workbook[name]
    rows = list(sheet.iter_rows(values_only=True))
    return list(rows[0]), [list(row) for row in rows[1:] if any(v is not None for v in row)]

def same_value(actual, expected, field):
    if field == "数据问题":
        actual = "" if actual is None else actual
        expected = "" if expected is None else expected
    if actual is None or expected is None:
        return actual is None and expected is None, None
    if isinstance(actual, Real) and isinstance(expected, Real):
        if not math.isfinite(float(actual)) or not math.isfinite(float(expected)):
            return actual == expected, None
        difference = abs(float(actual) - float(expected))
        return difference <= TOLERANCE, difference
    return type(actual) is type(expected) and actual == expected, None

def compare_workbooks(actual_path):
    import openpyxl
    actual_book = openpyxl.load_workbook(actual_path, read_only=True, data_only=True)
    expected_book = openpyxl.load_workbook(REFERENCE_XLSX, read_only=True, data_only=True)
    results = {}
    all_passed = set(actual_book.sheetnames) == set(expected_book.sheetnames)
    main_records = []
    for name in expected_book.sheetnames:
        if name not in actual_book:
            results[name] = {"passed": False, "reason": "missing sheet"}
            continue
        ah, ar = table_rows(actual_book, name)
        eh, er = table_rows(expected_book, name)
        schema_ok = set(ah) == set(eh) and len(ah) == len(eh)
        actual_records = [dict(zip(ah, row)) for row in ar]
        expected_records = [dict(zip(eh, row)) for row in er]
        key = "管线编号" if name == "健康评价总表" else eh[0]
        actual_ids = [r[key] for r in actual_records]
        expected_ids = [r[key] for r in expected_records]
        unique = len(set(actual_ids)) == len(actual_ids) and len(set(expected_ids)) == len(expected_ids)
        ids_match = set(actual_ids) == set(expected_ids)
        differences = []
        difference_count = 0
        max_numeric = 0.0
        by_id = {r[key]: r for r in actual_records}
        for expected in expected_records:
            actual = by_id.get(expected[key])
            if actual is None:
                continue
            for field in eh:
                matched, delta = same_value(actual.get(field), expected[field], field)
                if delta is not None:
                    max_numeric = max(max_numeric, delta)
                if not matched:
                    difference_count += 1
                    if len(differences) < 100:
                        differences.append({"id": expected[key], "field": field,
                                            "expected": expected[field], "actual": actual.get(field)})
        passed = schema_ok and unique and ids_match and len(ar) == len(er) and difference_count == 0
        all_passed = all_passed and passed
        results[name] = {"passed": passed, "actual_rows": len(ar), "reference_rows": len(er),
                         "actual_columns": len(ah), "schema_matches": schema_ok,
                         "unique_keys": unique, "id_sets_match": ids_match,
                         "row_order_matches": actual_ids == expected_ids,
                         "cell_difference_count": difference_count,
                         "max_numeric_difference": max_numeric, "difference_examples": differences}
        if name == "健康评价总表":
            main_records = actual_records
    actual_book.close()
    expected_book.close()
    return {"passed": all_passed, "numeric_tolerance": TOLERANCE,
            "normalization": "Only 数据问题: blank and empty string equivalent. Literal nan strings preserved.",
            "sheets": results}, main_records

def report_metrics(text):
    patterns = {
        "pipe_count": r"评价管线数：\s*(\d+)",
        "mean_health": r"平均健康度：\s*([\d.]+)",
        "mean_completeness": r"平均可信度：\s*([\d.]+)",
        "pipes_with_risk_points": r"有风险点的管线数：\s*(\d+)",
        "risk_point_attachment_sum": r"风险点总数：\s*(\d+)",
    }
    values = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match is None:
            raise ValueError("Missing report metric: " + key)
        values[key] = float(match.group(1)) if key.startswith("mean_") else int(match.group(1))
    return values

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default="2026-09-18T00:01:11+08:00",
                        help="Evaluation clock. Use 'now' for the actual current time.")
    args = parser.parse_args()
    started = datetime.now(CHINA)
    clock = started if args.as_of == "now" else datetime.fromisoformat(args.as_of)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=CHINA)
    clock = clock.astimezone(CHINA)
    run_id = "baseline_" + started.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    experiment = ROOT / "experiments/baseline" / run_id
    output_dir = ROOT / "outputs/baseline" / run_id
    experiment.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)
    log_path = experiment / "model.log"
    watched = [SOURCE, *INPUTS.values(), REFERENCE_XLSX, REFERENCE_TXT, ARCHIVE]
    hashes_before = {relative(path): digest(path) for path in watched}
    metadata = {
        "run_id": run_id, "status": "running", "server": "172.30.60.179:8888",
        "project_root": str(ROOT), "model_label": "baseline",
        "data_usage": "fixture_replay", "source_provenance": "provided_sample_unconfirmed",
        "started_at": started.isoformat(), "evaluation_clock": clock.isoformat(),
        "python_executable": sys.executable, "python_version": sys.version,
        "runner_sha256": digest(Path(__file__).resolve()),
        "requirements_sha256": digest(ROOT / "configs/baseline_requirements.txt"),
        "package_versions": {name: importlib.metadata.version(name)
                             for name in ("numpy", "pandas", "openpyxl")},
        "source_and_inputs_sha256_before": hashes_before,
        "invocation": [relative(Path(__file__).resolve()), "--as-of", args.as_of],
        "adaptation": "Original main executed completely. Desktop Path resolution redirected; datetime.now fixed to evaluation clock.",
        "log": relative(log_path), "output_directory": relative(output_dir),
    }
    write_json(experiment / "metadata.json", metadata)
    timer = time.perf_counter()
    print(json.dumps({"run_id": run_id, "status": "running", "log": relative(log_path)},
                     ensure_ascii=False), flush=True)
    try:
        metadata["extracted_copies_match_original_zip"] = verify_supplied_copies()
        class DesktopBridge:
            def exists(self):
                return True
            def __truediv__(self, filename):
                return INPUTS[filename] if filename in INPUTS else output_dir / filename

        desktop_candidates = {
            Path(os.path.expanduser("~")) / "Desktop",
            Path(os.path.expanduser("~")) / "OneDrive/Desktop",
            Path(os.path.expanduser("~")) / "OneDrive/桌面",
        }
        bridge = DesktopBridge()
        def path_adapter(*parts, **kwargs):
            value = Path(*parts, **kwargs)
            return bridge if value in desktop_candidates else value

        class EvaluationClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)

        module = ModuleType("provided_water_model")
        module.__file__ = str(SOURCE)
        module.__dict__["__name__"] = "provided_water_model"
        with log_path.open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), module.__dict__)
                module.Path = path_adapter
                module.datetime = EvaluationClock
                module.main()
        actual_xlsx = output_dir / "V3.0健康评价结果.xlsx"
        actual_txt = output_dir / "V3.0分析报告.txt"
        for path in (actual_xlsx, actual_txt):
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError("Expected model output missing: " + str(path))
        comparison, records = compare_workbooks(actual_xlsx)
        actual_report = report_metrics(actual_txt.read_text(encoding="utf-8"))
        reference_report = report_metrics(REFERENCE_TXT.read_text(encoding="utf-8-sig"))
        txt_passed = actual_report == reference_report
        comparison["text_report"] = {"passed": txt_passed,
                                      "actual_metrics": actual_report,
                                      "reference_metrics": reference_report,
                                      "generation_time_compared": False}
        comparison["passed"] = comparison["passed"] and txt_passed
        summary = {
            "pipe_count": len(records),
            "mean_health": statistics.fmean(float(r["健康度Hs"]) for r in records),
            "health_levels": dict(Counter(r["健康等级"] for r in records)),
            "mean_completeness": statistics.fmean(float(r["数据可信度(%)"]) for r in records),
            "pipes_with_risk_points": sum(r["风险点数量"] > 0 for r in records),
            "risk_point_attachment_sum": sum(r["风险点数量"] for r in records),
            "literal_nan_risk_id_rows": sum(r["风险点ID列表"] == "nan" for r in records),
        }
        import pandas as pd
        static = pd.read_excel(INPUTS["input_pipe_data.xlsx"])
        dynamic = pd.read_excel(INPUTS["input_pipe_data_动态.xlsx"])
        times = pd.to_datetime(dynamic["时间"])
        metadata["input_summary"] = {
            "static_rows": len(static), "dynamic_rows": len(dynamic),
            "unique_pipe_ids": int(static["管线编号"].nunique()),
            "dynamic_time_start": str(times.min()), "dynamic_time_end": str(times.max()),
            "dynamic_records_per_pipe": sorted(int(n) for n in dynamic.groupby("管线编号").size().unique()),
        }
        hashes_after = {relative(path): digest(path) for path in watched}
        metadata["protected_files_unchanged"] = hashes_after == hashes_before
        if not metadata["protected_files_unchanged"]:
            raise RuntimeError("A supplied source, input, or reference file changed")
        metadata["summary"] = summary
        metadata["comparison_passed"] = comparison["passed"]
        metadata["outputs"] = [
            {"path": relative(path), "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in (actual_xlsx, actual_txt)
        ]
        metadata["status"] = "passed" if comparison["passed"] else "comparison_failed"
        metadata["exit_code"] = 0 if comparison["passed"] else 2
        write_json(experiment / "comparison.json", comparison)
        write_json(experiment / "summary.json", summary)
        metadata["comparison"] = relative(experiment / "comparison.json")
    except BaseException as exc:
        metadata["status"] = "failed"
        metadata["exit_code"] = 1
        metadata["error"] = repr(exc)
        with log_path.open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
    metadata["elapsed_seconds"] = round(time.perf_counter() - timer, 3)
    metadata["finished_at"] = datetime.now(CHINA).isoformat()
    write_json(experiment / "metadata.json", metadata)
    evidence = ROOT / "02_核查证据/结构化记录" / (
        "提供模型完整运行核验_" + started.strftime("%Y%m%d_%H%M%S") + "_" + run_id.rsplit("_", 1)[-1] + ".json")
    if evidence.exists():
        raise FileExistsError(str(evidence))
    write_json(evidence, metadata)
    metadata["evidence_record"] = relative(evidence)
    write_json(experiment / "metadata.json", metadata)
    write_json(ROOT / "experiments/baseline/latest.json", {
        "run_id": run_id, "status": metadata["status"],
        "metadata": relative(experiment / "metadata.json"),
        "comparison": metadata.get("comparison"),
        "output_directory": relative(output_dir),
        "evidence_record": relative(evidence),
    })
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)
    return metadata["exit_code"]

if __name__ == "__main__":
    raise SystemExit(main())
