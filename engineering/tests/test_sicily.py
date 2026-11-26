from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.gas_application import sicily


def test_reachability_uses_pressure_boundaries_and_preserves_other_edges():
    nodes = {"1": {"pressure_pa": "7000000"}, "2": {"pressure_pa": ""}, "3": {"pressure_pa": ""}}
    pipes = {"a": {"inlet_index": "2", "outlet_index": "1"}, "b": {"inlet_index": "2", "outlet_index": "3"}}
    assert sicily.reachable(nodes, pipes) == {"1", "2", "3"}
    assert sicily.reachable(nodes, pipes, "b") == {"1", "2"}
    assert set(pipes) == {"a", "b"}


def test_snapshot_rejects_tampering_and_unknown_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("GAS_SICILY_DATA_ROOT", str(tmp_path))
    case_dir = tmp_path / sicily.DEFAULT
    case_dir.mkdir()
    nodes = b"node_index;pressure_pa\n1;7000000\n"
    path = case_dir / "sf_nodes.csv"
    path.write_bytes(nodes)
    manifest = {"dataset_id": sicily.ID, "commit": sicily.COMMIT,
                "cases": {sicily.DEFAULT: {path.name: hashlib.sha256(nodes).hexdigest()}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert sicily.case_files(sicily.DEFAULT)[1][path.name] == nodes
    with pytest.raises(ValueError, match="已登记"):
        sicily.case_files("../other")
    path.write_bytes(b"changed")
    with pytest.raises(OSError, match="校验失败"):
        sicily.case_files(sicily.DEFAULT)


def test_pinned_snapshot_if_installed(monkeypatch):
    local = Path(__file__).resolve().parents[1] / "outputs/research/sicily-snapshot"
    if not local.is_dir():
        pytest.skip("官方西西里快照未导入")
    monkeypatch.setenv("GAS_SICILY_DATA_ROOT", str(local))
    monkeypatch.delenv("SICILY_SOLVER_PYTHON", raising=False)
    overview = sicily.overview()
    assert overview["counts"] == {"nodes": 340, "connections": 350, "available_hours": 456}
    assert sum(item["published"] for item in overview["cases"]) == 436
    autumn = sicily.condition(sicily.DEFAULT)
    winter = sicily.condition("20260108-18")
    assert autumn["status"] == winter["status"] == "published"
    assert set(autumn["nodes"]) == {row["id"] for row in overview["nodes"]}
    assert autumn["nodes"]["8118"]["pressure_bar"] > 0
    result = sicily.run(sicily.DEFAULT, overview["edges"][0]["id"], overview["data_version"])
    assert result["hydraulics"]["status"] == "unavailable"
    monkeypatch.setenv("SICILY_SOLVER_PYTHON", "solver-should-not-be-started")
    def reject_solver(*args, **kwargs):
        pytest.fail("fast topology must not start a hydraulic subprocess")
    monkeypatch.setattr(sicily.subprocess, "run", reject_solver)
    fast = sicily.run(sicily.DEFAULT, "1", overview["data_version"], solve_hydraulics=False)
    assert fast["hydraulics"]["status"] == "not_requested"
    assert fast["reachable_before"] == 340
    assert fast["reachable_after"] == 339
    assert len(fast["unreachable_nodes"]) == 1
    bypass = sicily.run(sicily.DEFAULT, "342", overview["data_version"], solve_hydraulics=False)
    branch = sicily.run(sicily.DEFAULT, "254", overview["data_version"], solve_hydraulics=False)
    assert bypass["reachable_after"] == 340
    assert "342" not in bypass["alternate_path_edges"] and len(bypass["alternate_path_edges"]) == 10
    assert branch["reachable_after"] == 316
    assert len(branch["disconnected_branch_edges"]) == 23
    assert not branch["alternate_path_edges"]
    assert result["execution_id"] != fast["execution_id"]
    with pytest.raises(ValueError, match="不匹配"):
        sicily.run(sicily.DEFAULT, result["edge_id"], "old-snapshot")
