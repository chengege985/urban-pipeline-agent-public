from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.gas_application.agent import GasAgentQueryRequest, GasAgentService
from src.gas_application.public_data import (
    DRYAD_DATASET_ID,
    PHMSA_DATASET_ID,
    PUBLIC_CORPUS_ID,
    GasPublicDataService,
)
from src.gas_application.service import GasFixtureService
from src.gas_application.standalone import create_gas_app
from src.platform.authz import resolve_actor


def _zip(path: Path, members: dict[str, bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_fixture(root: Path) -> None:
    phmsa_text = (
        "REPORT_NUMBER\tIYEAR\tIDATE\tNAME\tACSTATE\tACCITY\tCAUSE_TEXT\n"
        "R-1\t2020\t20200102\tOperator A\tTX\tAustin\tOutside force\n"
        "R-2\t2021\t20210603\tOperator B\tCA\tFresno\tCorrosion\n"
    ).encode()
    phmsa_path = root / "raw/US-PHMSA/GAS-DISTRIBUTION/gas.zip"
    phmsa_sha = _zip(phmsa_path, {"incident.txt": phmsa_text, "fields.txt": b"fields"})
    (root / "receipts").mkdir(parents=True, exist_ok=True)
    (root / "receipts/phmsa_manual_download_20261124.json").write_text(json.dumps({
        "rows": [{
            "category": "GAS-DISTRIBUTION",
            "path": "GAS-DISTRIBUTION/gas.zip",
            "sha256": phmsa_sha,
        }]
    }), encoding="utf-8")

    csv_content = (
        "Date,Time,[CH4]_ppm,dis_m,PG\n"
        "2022-04-25,12:00:00,2.1,7.07,A\n"
        "2022-04-25,12:00:01,,7.07,A\n"
    ).encode()
    dryad_path = root / "raw/US-DRYAD-CH4/dryad.zip"
    dryad_sha = _zip(dryad_path, {
        "Experiment_1.UGGA.MET.2022-04-25.csv": csv_content,
        "README.md": b"Dryad readme",
    })
    (root / "receipts/dryad_manual_download_20261124.json").write_text(json.dumps({
        "path": "US-DRYAD-CH4/dryad.zip",
        "sha256": dryad_sha,
        "version": "test",
    }), encoding="utf-8")


def configure_principals(monkeypatch):
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "reader": {
            "actor_id": "reader",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": ["syn-gas-network-html54-0.1.0"],
            "corpora": [PUBLIC_CORPUS_ID],
        },
        "limited": {
            "actor_id": "limited",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": ["syn-gas-network-html54-0.1.0"],
            "corpora": [],
        },
    }))


def test_phmsa_dryad_quality_and_evidence(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    reader = resolve_actor("reader", "internal")
    service = GasPublicDataService(root=tmp_path)

    catalog = service.catalog(reader)
    assert catalog.status == "ready"
    assert {item.dataset_id for item in catalog.datasets} == {PHMSA_DATASET_ID, DRYAD_DATASET_ID}

    phmsa = service.phmsa_incidents(reader, category="gas_distribution", year_from=2020)
    assert phmsa.status == "succeeded"
    assert phmsa.total == 2
    assert phmsa.items[0]["record_id"] == "R-1"
    assert phmsa.items[0]["data_mode"] == "historical_us"
    assert phmsa.evidence_refs[0].startswith("phmsa:")

    dryad = service.dryad_quality(reader)
    assert dryad.status == "checked"
    assert dryad.total_records == 2
    assert dryad.experiments[0].missing_counts["[CH4]_ppm"] == 1
    report = service.report(reader, DRYAD_DATASET_ID)
    assert report.status == "checked"
    assert report.report_sha256
    assert report.evidence_refs


def test_public_agent_queries_are_read_only_and_acl_scoped(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    reader = resolve_actor("reader", "internal")
    agent = GasAgentService(
        GasFixtureService(),
        public_data_service=GasPublicDataService(root=tmp_path),
    )

    phmsa = agent.query(
        GasAgentQueryRequest(question="查询PHMSA美国历史燃气事故", tool_hint="phmsa.incident.query"),
        principal=reader,
    )
    assert phmsa.status == "succeeded"
    assert phmsa.data_mode == "public_reference"
    assert phmsa.dataset_id == PHMSA_DATASET_ID
    assert phmsa.tools[0].tool_name == "phmsa.incident.query"
    assert phmsa.evidence_refs

    dryad = agent.query(
        GasAgentQueryRequest(question="回放Dryad甲烷实验 Experiment_1"),
        principal=reader,
    )
    assert dryad.status == "succeeded"
    assert dryad.dataset_id == DRYAD_DATASET_ID
    assert dryad.tools[0].tool_name == "dryad.experiment.query"

    limited = resolve_actor("limited", "internal")
    with pytest.raises(HTTPException) as denied:
        GasPublicDataService(root=tmp_path).catalog(limited)
    assert denied.value.status_code == 403


def test_public_data_hash_mismatch_is_unavailable(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    path = tmp_path / "raw/US-PHMSA/GAS-DISTRIBUTION/gas.zip"
    path.write_bytes(path.read_bytes() + b"tampered")
    reader = resolve_actor("reader", "internal")
    quality = GasPublicDataService(root=tmp_path).phmsa_quality(reader)
    assert quality.status == "unavailable"
    assert quality.total_records == 0


def test_public_data_fastapi_routes_are_read_only_and_acl_scoped(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    monkeypatch.setenv("GAS_PUBLIC_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_TRUSTED_HOSTS", "testserver,127.0.0.1")
    app = create_gas_app()
    with TestClient(app) as client:
        headers = {"X-Water-Token": "reader"}
        catalog = client.get("/api/v1/gas/engineering/public-data/catalog", headers=headers)
        assert catalog.status_code == 200
        assert catalog.json()["status"] == "ready"
        incidents = client.get(
            "/api/v1/gas/engineering/public-data/phmsa/incidents?category=gas_distribution",
            headers=headers,
        )
        assert incidents.status_code == 200
        assert incidents.json()["total"] == 2
        report = client.get("/api/v1/gas/engineering/public-data/reports/US-DRYAD-CH4", headers=headers)
        assert report.status_code == 200
        assert report.json()["report_sha256"]
        denied = client.get(
            "/api/v1/gas/engineering/public-data/catalog", headers={"X-Water-Token": "limited"}
        )
        assert denied.status_code == 403
