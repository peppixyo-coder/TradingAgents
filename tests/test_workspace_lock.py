import multiprocessing

import pytest

from tradingagents.market_intelligence.workspace import WorkspaceStore, WorkspaceStoreError


def _hold_lock(root, ready, release):
    store = WorkspaceStore(root)
    with store._cross_process_lock():
        ready.set()
        release.wait(5)


def payload(name="lock test"):
    return {"schema_version": 1, "name": name, "ui": {"asset": "BTC", "timeframe": "1h", "compare_assets": [], "search": "", "sort": "coin", "indicator_settings": {}}}


def test_distinct_process_timeout_release_and_revision_conflict(tmp_path):
    root = tmp_path / "workspaces"
    store = WorkspaceStore(root)
    created = store.create(payload())
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_lock, args=(root, ready, release))
    process.start()
    assert ready.wait(3)
    with pytest.raises(WorkspaceStoreError) as exc:
        store.update(created["workspace_id"], 1, payload("blocked"))
    assert exc.value.code == "workspace_storage_unavailable"
    release.set()
    process.join(5)
    assert process.exitcode == 0
    saved = store.update(created["workspace_id"], 1, payload("released"))
    assert saved["revision"] == 2
    with pytest.raises(WorkspaceStoreError) as conflict:
        store.update(created["workspace_id"], 1, payload("obsolete"))
    assert conflict.value.code == "workspace_conflict"
    assert not list(root.glob("*.tmp"))
    assert not (root / ".workspace.lock").exists()


def test_distinct_roots_do_not_share_lock_and_exception_cleans_up(tmp_path):
    first, second = WorkspaceStore(tmp_path / "one"), WorkspaceStore(tmp_path / "two")
    with first._cross_process_lock(), second._cross_process_lock():
        pass
    with pytest.raises(RuntimeError), first._cross_process_lock():
        raise RuntimeError("boom")
    assert not (first.root / ".workspace.lock").exists()
