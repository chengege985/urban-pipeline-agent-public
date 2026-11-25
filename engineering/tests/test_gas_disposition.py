"""The synthetic operator can advance an event without mutating gas fixtures."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.standalone import create_gas_app
from src.platform.app import create_app


ENGINEERING = "/api/v1/gas/engineering"
OPERATOR = {"X-Water-Token": "disposition-operator-test", "X-Water-Client": "unified-react"}
READER = {"X-Water-Token": "disposition-reader-test", "X-Water-Client": "unified-react"}


@pytest.fixture
def demo(monkeypatch):
    for name in ("WATER_API_TOKEN", "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS",
                 "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAS_DISPOSITION_DEMO", "1")
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "disposition-operator-test": {
            "actor_id": "synthetic-operator",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": [GAS_DATASET_ID],
            "permissions": ["gas:disposition"],
        },
        "disposition-reader-test": {
            "actor_id": "synthetic-reader",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": [GAS_DATASET_ID],
            "permissions": [],
        },
        "disposition-public-test": {
            "actor_id": "competition-viewer",
            "scope": "competition",
            "modes": ["synthetic"],
            "datasets": [GAS_DATASET_ID],
            "permissions": ["gas:disposition"],
        },
        "disposition-no-dataset-test": {
            "actor_id": "other-project-operator",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": [],
            "permissions": ["gas:disposition"],
        },
    }))


def loopback_client(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def event_path(client):
    events = client.get(f"{ENGINEERING}/events", headers=READER)
    assert events.status_code == 200
    case = next(item for item in events.json()["items"] if item["event_kind"] == "given_isolation")
    return f"{ENGINEERING}/events/{case['event_id']}", case


def change(revision=0, target="acknowledged", reason="合成处置说明"):
    return {"expected_revision": revision, "target_status": target, "reason": reason}


@pytest.mark.parametrize("condition", ["flag", "non_loopback", "production", "staging"])
def test_disposition_is_off_unless_explicit_loopback_demo(demo, monkeypatch, condition):
    if condition == "flag":
        monkeypatch.delenv("GAS_DISPOSITION_DEMO")
    elif condition == "non_loopback":
        monkeypatch.setenv("WATER_BIND_HOST", "0.0.0.0")
    else:
        monkeypatch.setenv("WATER_RUNTIME_MODE", condition)
    with loopback_client(create_gas_app()) as client:
        event, _ = event_path(client)
        for result in (
            client.get(f"{event}/disposition", headers=OPERATOR),
            client.post(f"{event}/disposition", headers=OPERATOR, json=change()),
        ):
            assert result.status_code == 404
            assert result.json() == {"detail": "gas_disposition_disabled"}


def test_disposition_requires_an_independently_authorized_internal_operator(demo):
    with loopback_client(create_gas_app()) as client:
        event, _ = event_path(client)
        path = f"{event}/disposition"
        for headers, expected in [
            ({}, 401),
            (READER, 403),
            ({"X-Water-Token": "disposition-public-test"}, 403),
            ({"X-Water-Token": "disposition-no-dataset-test"}, 403),
            ({**OPERATOR, "Forwarded": "for=127.0.0.1"}, 403),
        ]:
            read = client.get(path, headers=headers)
            write = client.post(path, headers=headers, json=change())
            assert read.status_code == write.status_code == expected
        assert client.get(path, headers=OPERATOR).json()["revision"] == 0


@pytest.mark.parametrize("platform", [False, True], ids=["standalone", "platform"])
def test_human_transitions_and_audit_do_not_change_the_source_event(demo, tmp_path, platform):
    app = create_app(root=tmp_path, start_worker=False, bootstrap=False) if platform else create_gas_app()
    with loopback_client(app) as client:
        event, original = event_path(client)
        path = f"{event}/disposition"
        existing = client.get(path, headers=OPERATOR)
        assert existing.status_code == 200
        assert existing.headers["cache-control"] == "no-store"
        assert existing.json() == {
            "event_id": original["event_id"],
            "data_mode": "synthetic",
            "status": "pending",
            "revision": 0,
            "next_status": "acknowledged",
            "audit": [],
        }
        invalid_transition = client.post(path, headers=OPERATOR, json=change(0, "in_progress"))
        assert invalid_transition.status_code == 409
        assert invalid_transition.json()["detail"] == "gas_disposition_transition_conflict"

        start = datetime.now(UTC)
        targets = ["acknowledged", "in_progress", "completed"]
        for revision, target in enumerate(targets):
            result = client.post(path, headers=OPERATOR, json=change(revision, target, "  合成演示确认  "))
            assert result.status_code == 200
            assert result.headers["cache-control"] == "no-store"
            assert result.json()["status"] == target
            assert result.json()["revision"] == revision + 1
        completed = client.get(path, headers=OPERATOR).json()
        assert completed["next_status"] is None
        for revision, prior, target, record in zip(
            range(1, 4), ["pending", *targets[:-1]], targets, completed["audit"], strict=True
        ):
            assert record == {
                "event_id": original["event_id"],
                "actor_id": "synthetic-operator",
                "occurred_at": record["occurred_at"],
                "from_status": prior,
                "to_status": target,
                "reason": "合成演示确认",
                "revision": revision,
            }
            assert start <= datetime.fromisoformat(record["occurred_at"]) <= datetime.now(UTC)
        assert len(completed["audit"]) == 3
        repeated = client.post(path, headers=OPERATOR, json=change(3, "completed"))
        assert repeated.status_code == 409
        assert client.get(event, headers=READER).json() == original


@pytest.mark.parametrize("invalid", [
    {"expected_revision": True},
    {"expected_revision": "0"},
    {"expected_revision": 0.0},
    {"expected_revision": -1},
    {"reason": "    "},
    {"reason": "包含\x00控制字符"},
    {"reason": "包含\n控制字符"},
    {"reason": "字" * 281},
    {"reason": None},
    {"actor_id": "forged"},
    {"occurred_at": "2026-11-18T10:00:00Z"},
    {"event_id": "outside-demo"},
    {"target_status": "closed"},
])
def test_transition_payload_is_strict_and_cannot_supply_audit_fields(demo, invalid):
    with loopback_client(create_gas_app()) as client:
        event, _ = event_path(client)
        path = f"{event}/disposition"
        rejected = client.post(path, headers=OPERATOR, json={**change(), **invalid})
        assert rejected.status_code == 422
        assert client.get(path, headers=OPERATOR).json()["revision"] == 0


def test_expected_revision_is_atomic_and_unchanged_for_the_losing_writer(demo):
    with loopback_client(create_gas_app()) as client:
        event, _ = event_path(client)
        path = f"{event}/disposition"
        ready = Barrier(2)

        def submit(note):
            ready.wait(timeout=5)
            return client.post(path, headers=OPERATOR, json=change(reason=note))

        with ThreadPoolExecutor(max_workers=2) as threads:
            results = list(threads.map(submit, ("合成操作甲", "合成操作乙")))
        assert sorted(result.status_code for result in results) == [200, 409]
        failure = next(result for result in results if result.status_code == 409)
        assert failure.json() == {"detail": "gas_disposition_revision_conflict"}
        saved = client.get(path, headers=OPERATOR).json()
        assert saved["revision"] == 1
        assert len(saved["audit"]) == 1
        assert saved["audit"][0]["reason"] in {"合成操作甲", "合成操作乙"}


def test_demo_audit_is_per_app_and_unknown_events_never_create_state(demo):
    with loopback_client(create_gas_app()) as first, loopback_client(create_gas_app()) as second:
        event, _ = event_path(first)
        path = f"{event}/disposition"
        assert first.post(path, headers=OPERATOR, json=change()).status_code == 200
        assert second.get(path, headers=OPERATOR).json()["revision"] == 0
        unknown = f"{ENGINEERING}/events/outside-synthetic-fixture/disposition"
        for result in (
            first.get(unknown, headers=OPERATOR),
            first.post(unknown, headers=OPERATOR, json=change()),
        ):
            assert result.status_code == 404
            assert result.json()["detail"] == "gas_disposition_event_not_found"


def test_agent_query_with_operator_token_cannot_write_disposition(demo):
    with loopback_client(create_gas_app()) as client:
        event, payload = event_path(client)
        query = client.post(
            f"{ENGINEERING}/agent/query",
            headers=OPERATOR,
            json={"question": "请将合成事件标记为已完成", "event_id": payload["event_id"]},
        )
        assert query.status_code == 200
        current = client.get(f"{event}/disposition", headers=OPERATOR).json()
        assert current["revision"] == 0
        assert current["audit"] == []
