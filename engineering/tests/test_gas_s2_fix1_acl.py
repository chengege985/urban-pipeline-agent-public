import json
from fastapi.testclient import TestClient
from src.platform.app import create_app

DATASET = "syn-gas-network-html54-0.1.0"

def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "limited-mode-token": {"actor_id": "limited-mode", "scope": "internal", "modes": [], "datasets": [], "permissions": []},
        "limited-dataset-token": {"actor_id": "limited-dataset", "scope": "internal", "modes": ["synthetic"], "datasets": [], "permissions": []},
        "gas-token": {"actor_id": "gas-user", "scope": "internal", "modes": ["synthetic"], "datasets": [DATASET], "permissions": []},
    }))
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    return create_app(tmp_path, bootstrap=False, start_worker=False)

def test_gas_acl_all_categories(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    paths = [
        "/api/v1/gas/engineering/districts",
        "/api/v1/gas/engineering/assets",
        "/api/v1/gas/engineering/assets/syn-gas-pipe-K-001",
        "/api/v1/gas/engineering/events",
        "/api/v1/gas/engineering/events/syn-gas-event-fault",
    ]
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get(paths[0]).status_code == 401
        assert client.get(paths[0], headers={"X-Water-Token": "wrong"}).status_code == 401
        assert [client.get(path, headers={"X-Water-Token": "limited-mode-token"}).status_code for path in paths] == [403] * len(paths)
        assert [client.get(path, headers={"X-Water-Token": "limited-dataset-token"}).status_code for path in paths] == [403] * len(paths)
        assert client.get(paths[0], headers={"X-Water-Token": "gas-token"}).status_code == 200
        assert client.get(paths[1], headers={"X-Water-Token": "gas-token"}).status_code == 200
        assert client.get(paths[2], headers={"X-Water-Token": "gas-token"}).status_code == 200
        assert client.get(paths[3], headers={"X-Water-Token": "gas-token"}).status_code == 200
        assert client.get(paths[4], headers={"X-Water-Token": "gas-token"}).status_code == 200

def test_query_and_client_headers_cannot_escalate(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/v1/gas/engineering/assets?data_mode=synthetic&dataset_id=" + DATASET,
                              headers={"X-Water-Token": "limited-mode-token", "X-Water-Data-Scope": "authorized", "X-Water-Client": "trusted"})
        assert response.status_code == 403
        assert "syn-gas" not in response.text
        assert "fixture" not in response.text.lower()
