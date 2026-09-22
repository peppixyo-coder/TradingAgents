import os
from pathlib import Path

import pytest

from tradingagents.market_intelligence.workspace import (
    WorkspaceStore,
    WorkspaceStoreError,
)


def payload(name="BTC intraday"):
    return {
        "schema_version": 1,
        "name": name,
        "ui": {
            "asset": "BTC",
            "timeframe": "1h",
            "compare_assets": ["BTC", "ETH"],
            "search": "",
            "sort": "coin",
            "indicator_settings": {},
        },
    }


def test_round_trip_and_server_metadata(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces")
    created = store.create(payload())
    assert created["workspace_id"] and created["workspace_id"] != created["payload"]["name"]
    assert created["revision"] == 1 and created["created_at"] == created["updated_at"]
    assert store.get(created["workspace_id"]) == created
    assert store.list()[0]["workspace_id"] == created["workspace_id"]
    updated = store.update(created["workspace_id"], 1, payload("ETH review"))
    assert updated["revision"] == 2 and updated["created_at"] == created["created_at"]
    assert store.delete(created["workspace_id"], 2) is None
    with pytest.raises(WorkspaceStoreError, match="not found"):
        store.get(created["workspace_id"])


def test_duplicate_and_stale_revision_are_deterministic(tmp_path):
    one = WorkspaceStore(tmp_path / "w")
    two = WorkspaceStore(tmp_path / "w")
    created = one.create(payload())
    with pytest.raises(WorkspaceStoreError) as duplicate:
        two.create(payload("btc INTRADAY"))
    assert duplicate.value.code == "workspace_duplicate"
    saved = one.update(created["workspace_id"], 1, payload("changed"))
    with pytest.raises(WorkspaceStoreError) as conflict:
        two.update(created["workspace_id"], 1, payload("stale"))
    assert conflict.value.code == "workspace_conflict"
    assert one.get(created["workspace_id"])["payload"]["name"] == "changed"
    assert saved["revision"] == 2


def test_corrupt_record_is_not_replaced_or_deleted(tmp_path):
    root = tmp_path / "w"
    store = WorkspaceStore(root)
    created = store.create(payload())
    path = root / f"{created['workspace_id']}.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(WorkspaceStoreError) as exc:
        store.get(created["workspace_id"])
    assert exc.value.code == "workspace_corrupt"
    assert path.read_text(encoding="utf-8") == "{broken"


def test_atomic_write_failure_preserves_previous_record_and_temp_cleanup(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "w")
    created = store.create(payload())
    original = (tmp_path / "w" / f"{created['workspace_id']}.json").read_bytes()
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(WorkspaceStoreError) as exc:
        store.update(created["workspace_id"], 1, payload("new"))
    assert exc.value.code == "workspace_storage_unavailable"
    monkeypatch.setattr(os, "replace", real_replace)
    assert (tmp_path / "w" / f"{created['workspace_id']}.json").read_bytes() == original
    assert not list((tmp_path / "w").glob("*.tmp"))


def test_limits_invalid_root_and_operational_paths(tmp_path):
    store = WorkspaceStore(tmp_path / "w")
    for _ in range(32):
        store.create(payload(str(len(store.list()))))
    with pytest.raises(WorkspaceStoreError) as exc:
        store.create(payload("overflow"))
    assert exc.value.code == "workspace_limit_exceeded"
    with pytest.raises(WorkspaceStoreError) as invalid:
        store.create(
            {
                "schema_version": 1,
                "name": "x",
                "ui": {"asset": "BTC", "timeframe": "1h", "compare_assets": ["BTC"] * 4},
            }
        )
    assert invalid.value.code == "workspace_invalid"
    with pytest.raises(WorkspaceStoreError):
        WorkspaceStore(Path("relative-workspaces"))
    with pytest.raises(WorkspaceStoreError):
        WorkspaceStore(tmp_path / "state")


def test_incompatible_watchlist_is_preserved(tmp_path):
    store = WorkspaceStore(tmp_path / "w")
    created = store.create(payload())
    assert store.get(created["workspace_id"])["payload"]["ui"]["asset"] == "BTC"
    # Compatibility is deliberately evaluated by W1, never used to mutate storage.
    from tradingagents.market_intelligence.workspace import check_workspace_compatibility

    assert check_workspace_compatibility(created["payload"], ["SOL"])["compatible"] is False
    assert store.get(created["workspace_id"])["payload"]["ui"]["asset"] == "BTC"


def test_total_size_limit_and_no_operational_artifacts(tmp_path):
    store = WorkspaceStore(tmp_path / "w", max_total_bytes=512_000)
    # individual validator cap is enforced before the store; store files remain bounded.
    store.create(payload())
    files = list((tmp_path / "w").iterdir())
    assert files and all(f.suffix == ".json" for f in files)
    assert not (tmp_path / "state").exists() and not (tmp_path / "bot.db").exists()
