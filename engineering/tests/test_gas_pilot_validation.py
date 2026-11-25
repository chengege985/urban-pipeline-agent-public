"""Admission and formal approval/review rules tested with synthetic records only."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from pilot02_fixture import DATASET, FIXTURE, OBSERVATION, PIPE, SENSOR, build_synthetic_validation, set_row, time_at
from src.gas_application.data_package import IntakeError
from src.gas_application.model_provider import MODEL_ID, LocalGasModel
from src.gas_application.pilot_api import _approved
from src.gas_application.pilot_contract import PilotGrant, ROW_MODELS
from src.gas_application.pilot_intake import _validate_scope, inspect_pilot
from src.gas_application.pilot_replay import PilotReplayRequest, replay
from src.gas_application.pilot_store import PilotDatasetEntry, PilotStore
from src.gas_application.standalone import create_gas_app
from src.platform.authz import Principal

BASE = f"/api/v1/gas/engineering/pilot/datasets/{DATASET}/versions"
READER = {"X-Water-Token": "synthetic-pilot-reader"}


@pytest.fixture
def source(tmp_path):
    return build_synthetic_validation(tmp_path / "source")


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "pilot-store"
    root.mkdir(mode=0o700)
    return PilotStore(root)


def test_versioned_history_and_truth_comparison(source):
    inspection = inspect_pilot(source)
    assert inspection.report["issue_counts"]["error"] == 0
    assert sum(item["code"] == "threshold_not_approved" for item in inspection.report["issues"]) == 2
    result = replay(inspection.snapshot, PilotReplayRequest(sensor_id=SENSOR))
    assert [x.threshold.version for x in result.segments] == ["pilot-r1", "pilot-r2"]
    assert result.observations[13].observation.value is None and result.observations[13].classification == "excluded"
    assert result.observations[14].observation.quality == "suspect" and result.observations[14].classification == "excluded"
    assert [x.cause for x in result.segments[1].channel.events] == [
        "above_upper", "within_limits", "no_usable_observation", "observations_resumed", "above_upper", "within_limits",
    ]
    rule_change, = result.validation.duplicate_alarms
    assert rule_change.original.threshold_version == "pilot-r1" and rule_change.alarm.threshold_version == "pilot-r2"
    metrics = result.validation.metrics
    assert metrics.mode == "synthetic_truth_check"
    assert (len(metrics.false_alarms), metrics.missed_events, metrics.recoveries_detected, metrics.recoveries_missed) == (1, 1, 1, 1)
    assert metrics.false_alarms[0].at.minute == 18
    assert metrics.matches[0].detection_delay_seconds == 120
    assert metrics.matches[0].recovery_delay_seconds == 30
    assert metrics.matches[1].detection_delay_seconds is None


def test_incomplete_review_and_outside_use_remain_rule_only(source):
    snapshot = inspect_pilot(source).snapshot
    full = PilotReplayRequest(sensor_id=SENSOR)
    narrow = PilotReplayRequest(sensor_id=SENSOR, start=time_at(1), end=time_at(22))
    assert replay(snapshot, full, allow_truth=False).validation.reason == "review_use_outside_scope"
    assert replay(snapshot, narrow).validation.metrics is None

    set_row(source, "truth_windows", 0, coverage_status="partial")
    result = replay(inspect_pilot(source).snapshot, full)
    assert result.validation.status == "rule_only"
    assert result.validation.metrics is None


def test_sample_gap_breaks_deduplication_across_rule_versions(source):
    for index in (6, 7):
        set_row(source, "observations", index, value="", quality="missing")
    result = replay(inspect_pilot(source).snapshot, PilotReplayRequest(sensor_id=SENSOR))
    assert not result.validation.duplicate_alarms
    assert result.segments[0].channel.events[-1].kind == "data_missing"


@pytest.mark.parametrize(("table", "index", "changes", "code"), [
    ("threshold_rules", 0, {"approved_at": time_at(1)}, "invalid_record"),
    ("threshold_rules", 0, {"evidence_reference": ""}, "invalid_record"),
    ("threshold_rules", 0, {"unit": "MPa"}, "threshold_binding_mismatch"),
    ("threshold_rules", 0, {"asset_id": "syn-gas-pilot-pipe-b"}, "threshold_binding_mismatch"),
    ("threshold_rules", 1, {"effective_from": time_at(7)}, "overlapping_approved_policy"),
    ("truth_events", 1, {"event_id": "syn-gas-pilot-event-a"}, "duplicate_truth_event"),
    ("truth_events", 1, {"started_at": time_at(22)}, "invalid_record"),
])
def test_malformed_approved_threshold_or_truth_is_rejected_and_audited(source, store, table, index, changes, code):
    set_row(source, table, index, **changes)
    inspection = inspect_pilot(source)
    assert inspection.snapshot is None
    assert code in {item["code"] for item in inspection.report["issues"]}
    assert store.prepare(inspection, actor="synthetic-reviewer")["status"] == "rejected"
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT action,actor FROM admission_audit").fetchone() == ("rejected", "synthetic-reviewer")


def test_quarantine_rollback_and_exact_readmission(source, store):
    old, new = inspect_pilot(FIXTURE), inspect_pilot(source)
    for package in (old, new):
        assert store.prepare(package, actor="qa-ingest")["status"] == "imported"
    entry, _ = store.entry(DATASET, "demo-v2")
    assert entry.preferred and entry.imported_by == "qa-ingest"
    assert entry.manifest_sha256 == new.identity.manifest_sha256
    assert store.change_version(DATASET, "demo-v2", action="quarantine", actor="qa-custodian",
                                reason="synthetic exercise")["preferred_version"] is None
    with pytest.raises(IntakeError, match="dataset_quarantined"):
        store.snapshot(new.identity)
    with pytest.raises(IntakeError, match="dataset_requires_readmission"):
        store.prepare(new)
    assert store.change_version(DATASET, "demo-v1", action="rollback", actor="qa-custodian",
                                reason="restore legacy version")["preferred_version"] == "demo-v1"
    assert store.prepare(new, actor="qa-readmit", readmit=True)["status"] == "readmitted"
    assert store.entry(DATASET, "demo-v2")[0].preferred
    assert store.snapshot(new.identity) == new.snapshot
    set_row(source, "observations", 3, value="7.1")
    with pytest.raises(IntakeError, match="dataset_version_conflict"):
        store.prepare(inspect_pilot(source), actor="qa-readmit", readmit=True)

    with sqlite3.connect(store.database) as db:
        rows = db.execute("SELECT action,actor,previous_head,at FROM admission_audit ORDER BY receipt").fetchall()
    assert [(action, actor, previous) for action, actor, previous, _ in rows] == [
        ("imported", "qa-ingest", None), ("imported", "qa-ingest", "demo-v1"),
        ("quarantine", "qa-custodian", "demo-v2"), ("rollback", "qa-custodian", None),
        ("readmitted", "qa-readmit", "demo-v1"),
    ]
    assert all(datetime.fromisoformat(at).tzinfo is not None for _, _, _, at in rows)


def test_real_grant_metadata_and_scope_fail_closed_without_admitting_real_records(source, store, monkeypatch):
    inspection = inspect_pilot(source)
    synthetic = inspection.snapshot
    now = datetime.now(UTC)
    identity = inspection.identity.model_copy(update={"dataset_id": "qa-grant-metadata-only", "data_mode": "real_authorized"})
    fields = dict(
        approval_ref="qa-approval", scope_ref="qa-scope", directory=str(source),
        dataset_id=identity.dataset_id, version=identity.version, manifest_sha256=identity.manifest_sha256,
        valid_from=now - timedelta(hours=1), valid_until=now + timedelta(hours=1),
        subjects=["qa-reader"], import_operators=["qa-importer"], region_ref="qa-region",
        asset_ids=[PIPE], uses=["historical_replay"], history_from=time_at(0), history_to=time_at(8),
    )
    grant = PilotGrant.model_validate(fields)
    for kind, changes in (
        ("subjects", {"subjects": []}),
        ("asset_ids", {"asset_ids": []}),
        ("uses", {"uses": ["asset_geometry"]}),
        ("history_to", {"history_to": time_at(0)}),
    ):
        with pytest.raises(ValidationError, match=kind if kind in {"subjects", "asset_ids"} else "replay scope"):
            PilotGrant.model_validate({**fields, **changes})
    monkeypatch.setattr(store, "grants", lambda: (grant,))
    assert store.approved(identity, "qa-approval") == grant
    with pytest.raises(IntakeError, match="approval_required"):
        store.approved(identity.model_copy(update={"manifest_sha256": "f" * 64}), "qa-approval")
    with pytest.raises(IntakeError, match="import_operator_not_approved"):
        store._actor("qa-reader", grant)
    assert store._actor("qa-importer", grant) == "qa-importer"
    entry = PilotDatasetEntry(**identity.model_dump(), imported_at=now, quality_status="partial",
                              pipes=0, sensors=0, observations=0, grant_scope_sha256=grant.scope_digest())
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    with pytest.raises(HTTPException) as error:
        _approved(store, entry, "qa-approval", Principal("qa-importer", True, frozenset({"real_authorized"})))
    assert error.value.status_code == 403
    assert _approved(store, entry, "qa-approval", Principal("qa-reader", True, frozenset({"real_authorized"}))) == grant
    monkeypatch.setattr(store, "grants", lambda: (grant.model_copy(update={"region_ref": "qa-changed-region"}),))
    with pytest.raises(HTTPException) as error:
        _approved(store, entry, "qa-approval", Principal("qa-reader", True, frozenset({"real_authorized"})))
    assert error.value.status_code == 409
    monkeypatch.setattr(store, "grants", lambda: (grant.model_copy(update={"valid_until": now}),))
    with pytest.raises(IntakeError, match="approval_required"):
        store.approved(identity, "qa-approval")

    issues = set()
    rows = {table: {getattr(point, key): point for point in getattr(synthetic, table)}
            for table, (_, key) in ROW_MODELS.items()}
    _validate_scope(rows, grant, lambda _, code, *args: issues.add(code))
    assert {"asset_outside_approved_scope", "region_outside_approved_scope", "time_outside_approved_scope"} <= issues


def test_http_turns_require_same_manifest_observation_and_threshold(source, store, monkeypatch):
    old, new = inspect_pilot(FIXTURE), inspect_pilot(source)
    store.prepare(old)
    store.prepare(new)
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("GAS_PILOT_STORE", str(store.root))
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        READER["X-Water-Token"]: {"actor_id": "synthetic-reader", "modes": ["synthetic"], "datasets": [DATASET]},
    }))
    seen = []

    def complete(request):
        seen.append(json.loads(json.loads(request.content)["messages"][-1]["content"]))
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [
            {"message": {"content": '{"sentence_ids":[3,4]}'}},
        ]})

    model = LocalGasModel("http://127.0.0.1:12001", "synthetic", transport=httpx.MockTransport(complete))
    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: model)
    query = f"{BASE}/demo-v2/agent/query"
    payload = {"question": "这个阈值来自哪里？", "observation_id": OBSERVATION,
               "binding": {"manifest_sha256": new.identity.manifest_sha256, "threshold_version": "pilot-r1"}}
    with TestClient(create_gas_app()) as http:
        assert http.post(query, json=payload).status_code == 401
        assert http.post(f"{BASE}/demo-v1/agent/query", json=payload, headers=READER).status_code == 409
        assert http.post(query, json={**payload, "observation_id": "syn-gas-pilot-obs-p10"},
                         headers=READER).status_code == 409
        assert not seen
        reply = http.post(query, json=payload, headers=READER)
        assert reply.status_code == 200
        result = reply.json()
        assert result["parser_source"] == "qwen_evidence_selection" and result["threshold_version"] == "pilot-r1"
        assert "synthetic-threshold-reference-a1" in result["answer"] and "pilot-r2" not in result["answer"]
        assert seen[0]["question"] == payload["question"]
        assert {item["kind"] for item in result["citations"]} == {"observation", "event", "threshold", "asset", "sensor"}
        store.change_version(DATASET, "demo-v2", action="quarantine", actor="qa-test", reason="verify read denial")
        assert http.get(f"{BASE}/demo-v2", headers=READER).status_code == 409
        assert http.post(query, json=payload, headers=READER).status_code == 409
        assert len(seen) == 1


def test_operator_dictionary_covers_every_supported_handoff_field():
    import csv
    from src.gas_application.pilot_contract import PilotFile, PilotManifest

    path = Path(__file__).resolve().parents[2] / "docs/templates/gas-pilot-02-fields.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    keys = {("数据包清单", field) for field in PilotManifest.model_fields}
    keys |= {("文件映射", field) for field in PilotFile.model_fields}
    keys |= {("受控授权登记", field) for field in PilotGrant.model_fields}
    for name, (model, _) in ROW_MODELS.items():
        if name != "thresholds":
            keys |= {(name, field) for field in ("source_record_id", *(f for f in model.model_fields if f != "source"))}
    current_contract = [row for row in rows if row["交付级别"] in {"A", "B"}]
    assert keys == {(row["数据表"], row["字段名称"]) for row in current_contract}
    assert len(keys) == len(current_contract)
    assert {row["交付级别"] for row in rows} == {"A", "B", "C"}
    assert all(all(values and values.strip() for values in row.values()) for row in rows)
