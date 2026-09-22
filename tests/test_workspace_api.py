
from fastapi.testclient import TestClient

from dashboard import server
from tradingagents.market_intelligence.workspace import WorkspaceStore, WorkspaceStoreError


def payload(name="BTC Workspace", asset="BTC"):
    return {
        "schema_version": 1,
        "name": name,
        "ui": {
            "asset": asset,
            "timeframe": "1h",
            "compare_assets": [],
            "search": "",
            "sort": "coin",
            "indicator_settings": {},
        },
    }


def _hold_external_lock(root, ready, release):
    store = WorkspaceStore(root)
    with store._cross_process_lock():
        ready.set()
        release.wait(5)


def test_auth_reused_and_missing_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "secret-test-key")
    client = TestClient(server.app)

    # Missing API key -> 401
    res = client.get("/api/workspaces")
    assert res.status_code == 401

    # Invalid API key -> 401
    res = client.get("/api/workspaces", headers={"X-API-Key": "wrong"})
    assert res.status_code == 401

    # Correct API key -> 200
    res = client.get("/api/workspaces", headers={"X-API-Key": "secret-test-key"})
    assert res.status_code == 200
    assert res.json() == []


def test_workspace_crud_flow_and_revisions(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    # 1. GET list empty
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    assert res.json() == []

    # 2. POST valid
    res = client.post("/api/workspaces", json=payload())
    assert res.status_code == 200
    created = res.json()
    assert created["revision"] == 1
    w_id = created["workspace_id"]

    # 3. GET list contains metadata and payload
    res = client.get("/api/workspaces")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["workspace_id"] == w_id

    # 4. GET by id
    res = client.get(f"/api/workspaces/{w_id}")
    assert res.status_code == 200
    assert res.json() == created

    # 5. GET missing id -> 404
    res = client.get("/api/workspaces/nonexistent-id")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "workspace_not_found"

    # 6. PUT update with correct If-Match revision
    updated_payload = payload("Updated Name")
    res = client.put(f"/api/workspaces/{w_id}", json=updated_payload, headers={"If-Match": '"1"'})
    assert res.status_code == 200
    assert res.json()["revision"] == 2
    assert res.json()["payload"]["name"] == "Updated Name"

    # 7. PUT with stale revision -> 409
    res = client.put(f"/api/workspaces/{w_id}", json=updated_payload, headers={"If-Match": '"1"'})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "workspace_conflict"

    # 8. PUT without If-Match -> 428
    res = client.put(f"/api/workspaces/{w_id}", json=updated_payload)
    assert res.status_code == 428

    # 9. DELETE with stale revision -> 409
    res = client.delete(f"/api/workspaces/{w_id}", headers={"If-Match": '"1"'})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "workspace_conflict"

    # 10. DELETE with correct revision -> 204
    res = client.delete(f"/api/workspaces/{w_id}", headers={"If-Match": '"2"'})
    assert res.status_code == 204

    # 11. GET after delete -> 404
    res = client.get(f"/api/workspaces/{w_id}")
    assert res.status_code == 404


def test_server_fields_rejected_on_post_and_put(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    # Client tries to send server-managed fields on create -> 422
    for field in ("workspace_id", "revision", "created_at", "updated_at"):
        bad = {**payload(), field: "injection"}
        res = client.post("/api/workspaces", json=bad)
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "workspace_invalid"


def test_name_uniqueness_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    res = client.post("/api/workspaces", json=payload("Alpha Workspace"))
    assert res.status_code == 200

    # Same name with different casing -> 409 duplicate
    res = client.post("/api/workspaces", json=payload("alpha workspace"))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "workspace_duplicate"


def test_unconfigured_or_operational_storage_root(monkeypatch, tmp_path):
    # Unconfigured root -> 503
    monkeypatch.delenv("DASHBOARD_WORKSPACE_DIR", raising=False)
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    res = client.get("/api/workspaces")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "workspace_storage_unavailable"

    # Operational path rejection (e.g. pointing to state/ or bot.db) -> 503
    forbidden = tmp_path / "state" / "workspaces"
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(forbidden))
    res = client.get("/api/workspaces")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "workspace_storage_unavailable"


def test_corrupt_record_returns_explicit_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    # Write corrupt JSON into storage root
    corrupt_file = tmp_path / "corrupt-record.json"
    corrupt_file.write_text("{invalid_json", encoding="utf-8")

    res = client.get("/api/workspaces")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "workspace_corrupt"


def test_lock_unavailable_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")

    class LockedStore:
        def create(self, payload):
            raise WorkspaceStoreError("workspace_storage_unavailable", "workspace lock timeout")

    monkeypatch.setattr(server, "workspace_store", lambda: LockedStore())
    res = TestClient(server.app).post("/api/workspaces", json=payload())
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "workspace_storage_unavailable"


def test_workspace_count_limit_exceeded(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    # Max workspaces is 32 in store
    for i in range(32):
        res = client.post("/api/workspaces", json=payload(f"WS-{i}"))
        assert res.status_code == 200

    # 33rd workspace -> 413 limit exceeded
    res = client.post("/api/workspaces", json=payload("WS-32"))
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "workspace_limit_exceeded"


def test_unsupported_http_methods(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    client = TestClient(server.app)

    # PATCH is not implemented
    res = client.patch("/api/workspaces")
    assert res.status_code == 405


def test_payload_size_limit_exceeded(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", "")
    oversized = payload("Oversized")
    oversized["ui"]["search"] = "x" * 100_000
    res = TestClient(server.app).post("/api/workspaces", json=oversized)
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "workspace_limit_exceeded"
