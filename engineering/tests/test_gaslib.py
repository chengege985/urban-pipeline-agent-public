
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.gas_application.gaslib import GasLibService
from src.gas_application.agent import GasAgentQueryRequest, GasAgentService
from src.gas_application.public_data import GasPublicDataService
from src.gas_application.service import GasFixtureService
from src.platform.authz import resolve_actor


def _write_gaslib(root: Path, *, bad_endpoint: bool = False) -> str:
    archive_path = root / "raw/DE-GASLIB/GasLib-1-v1.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    net = """<?xml version="1.0"?>
<network xmlns="http://gaslib.zib.de/Gas" xmlns:f="http://gaslib.zib.de/Framework">
  <nodes>
    <source id="src" x="0" y="0"><flowMin unit="u" value="0"/></source>
    <sink id="sink" x="1" y="0"/>
    <innode id="n1" x="0.5" y="0"/>
  </nodes>
  <connections>
    <pipe id="p1" from="src" to="n1"><length unit="km" value="1"/><diameter unit="mm" value="100"/></pipe>
    <valve id="v1" from="n1" to="sink"/>
    <compressorStation id="cs1" from="src" to="n1"/>
  </connections>
</network>
"""
    if bad_endpoint:
        net = net.replace('to="n1"', 'to="missing"', 1)
    cs = """<compressorStations xmlns="http://gaslib.zib.de/CompressorStations"><compressorStation id="cs1"/></compressorStations>"""
    scn = """<boundaryValue xmlns="http://gaslib.zib.de/Gas"><scenario id="scenario-1"><node type="entry" id="src"><flow bound="lower" unit="u" value="1"/></node><node type="exit" id="sink"><flow bound="upper" unit="u" value="1"/></node></scenario></boundaryValue>"""
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("GasLib-1.net", net)
        archive.writestr("GasLib-1.cs", cs)
        archive.writestr("GasLib-1.scn", scn)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    receipts = root / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / "data_acceptance_20261124.json").write_text(json.dumps({
        "files": [{"path": "raw/DE-GASLIB/GasLib-1-v1.zip", "sha256": digest}]
    }), encoding="utf-8")
    return digest


def _principal(monkeypatch):
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "reader": {
            "actor_id": "reader", "scope": "internal", "modes": ["synthetic"],
            "datasets": [], "corpora": ["gas-public-20261124"],
        },
        "limited": {
            "actor_id": "limited", "scope": "internal", "modes": ["synthetic"],
            "datasets": [], "corpora": [],
        },
    }))
    return resolve_actor("reader", "internal")


def test_gaslib_quality_normalizes_and_verifies_cross_file_refs(tmp_path, monkeypatch):
    digest = _write_gaslib(tmp_path)
    principal = _principal(monkeypatch)
    service = GasLibService(root=tmp_path)
    instances = service.catalog(principal)
    assert instances[0].archive_sha256 == digest
    report = service.quality(principal, "GasLib-1-v1")
    assert report.status == "checked"
    assert report.node_count == 3
    assert report.source_count == 1
    assert report.sink_count == 1
    assert report.connection_count == 3
    assert report.scenarios_checked == 1
    assert report.scenario_boundary_count == 2
    assert report.normalized_scenarios[0]["scenario_id"] == "scenario-1"
    assert any(ref.startswith("gaslib:archive:") for ref in report.evidence_refs)
    assert report.member_hashes["GasLib-1.net"]


def test_gaslib_quality_marks_missing_endpoint(tmp_path, monkeypatch):
    _write_gaslib(tmp_path, bad_endpoint=True)
    principal = _principal(monkeypatch)
    report = GasLibService(root=tmp_path).quality(principal, "GasLib-1-v1")
    assert report.status == "partial"
    assert "pipe:p1:missing_to:missing" in report.quality_issues


def test_gaslib_acl_and_unknown_instance(tmp_path, monkeypatch):
    _write_gaslib(tmp_path)
    principal = _principal(monkeypatch)
    service = GasLibService(root=tmp_path)
    missing = service.quality(principal, "GasLib-unknown")
    assert missing.status == "not_found"
    limited = resolve_actor("limited", "internal")
    with pytest.raises(HTTPException) as denied:
        service.catalog(limited)
    assert denied.value.status_code == 403


def test_gaslib_agent_tool_returns_evidence(tmp_path, monkeypatch):
    _write_gaslib(tmp_path)
    principal = _principal(monkeypatch)
    agent = GasAgentService(
        GasFixtureService(),
        public_data_service=GasPublicDataService(root=tmp_path),
        gaslib_service=GasLibService(root=tmp_path),
    )
    result = agent.query(
        GasAgentQueryRequest(question="查询 GasLib-1 XML 跨文件引用", tool_hint="gaslib.quality.query"),
        principal=principal,
    )
    assert result.status == "succeeded"
    assert result.dataset_id == "DE-GASLIB"
    assert result.tools[0].tool_name == "gaslib.quality.query"
    assert result.evidence_refs
