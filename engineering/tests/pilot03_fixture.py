"""Extend the PILOT-02 synthetic replay with a source quality reason and a flow rule."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from pilot02_fixture import PREFIX, _read, _rule, _write, build_synthetic_validation, seal

FLOW = PREFIX + "flow-b"
FLOW_PIPE = PREFIX + "pipe-b"
SUSPECT = PREFIX + "obs-p14"
REASON = "合成样例标记：采集端自检未通过"


def build_workbench(path: Path) -> Path:
    build_synthetic_validation(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "demo-v3"
    source = path / "observations.csv"
    rows = _read(source)
    for row in rows:
        row["quality_reason"] = REASON if row["observation_id"] == SUSPECT else ""
    _write(source, rows)
    manifest["files"]["observations"]["columns"]["quality_reason"] = "quality_reason"
    manifest["files"]["observations"]["version"] = "fixture-v3"
    source = path / "threshold_rules.csv"
    rules = _read(source)
    rules.append({**_rule("flow-b", "pilot-flow-r1", 12, 0, 24),
                  "metric_id": "flow", "asset_id": FLOW_PIPE, "sensor_id": FLOW,
                  "unit": "Nm3/h", "threshold_type": "lower", "sustained_samples": 2})
    _write(source, rules)
    manifest["files"]["threshold_rules"]["version"] = "fixture-v3"
    source = path / "gis.geojson"
    geo = json.loads(source.read_text(encoding="utf-8"))
    geo["features"] = [feature for feature in geo["features"] if feature["properties"]["pipe_id"] != FLOW_PIPE]
    source.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    manifest["files"]["gis"]["version"] = "fixture-v3"
    seal(path, manifest)
    return path


def build_rejected(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "demo-v3-rejected"
    del manifest["files"]["observations"]["columns"]["observed_at"]
    seal(target, manifest)
    return target


if __name__ == "__main__":
    source = build_workbench(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        build_rejected(source, Path(sys.argv[2]))
