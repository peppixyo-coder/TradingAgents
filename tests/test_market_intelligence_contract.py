import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tradingagents.market_intelligence.contract import (
    ExternalSnapshot,
    SnapshotStatus,
    deterministic_snapshot_id,
    validate_snapshot,
)


def raw(**overrides):
    value = {
        "schema_version": 1,
        "snapshot_id": "fixture-1",
        "asset": "BTC",
        "canonical_asset": "BTC",
        "fetched_at": "2026-09-19T12:00:00Z",
        "as_of": "2026-09-19T11:59:00Z",
        "provider": "manual_export",
        "status": "ok",
        "data": {"price": 100, "volume": 0},
        "quality": {"freshness_seconds": 60, "source": "fixture",
                    "coverage": "full", "errors": []},
        "provenance": {"endpoint_or_query": "fixture", "license": "test",
                        "requires_api_key": False, "paid": False},
    }
    value.update(overrides)
    return value


def test_valid_snapshot_preserves_zero_and_serializes():
    out = validate_snapshot(raw())
    assert out["data"]["volume"] == 0
    assert json.loads(ExternalSnapshot(**raw()).to_json())["status"] == "ok"


def test_stale_requires_explicit_acceptance():
    with pytest.raises(ValueError, match="stale"):
        validate_snapshot(raw(status="stale"))
    assert validate_snapshot(raw(status="stale"), allow_stale=True)["status"] == "stale"


@pytest.mark.parametrize("bad", [raw(fetched_at="yesterday"), raw(data={"x": object()}),
                                  {"schema_version": 1}])
def test_malformed_snapshot_rejected(bad):
    with pytest.raises(ValueError):
        validate_snapshot(bad)


def test_secret_shaped_text_is_redacted():
    out = validate_snapshot(raw(data={"note": "api_key=do-not-store"}))
    assert "do-not-store" not in json.dumps(out)
    assert out["data"]["note"] == "[REDACTED]"


def test_snapshot_size_is_bounded():
    with pytest.raises(ValueError, match="maximum"):
        validate_snapshot(raw(data={str(i): "x" * 2_000 for i in range(100)}))


def test_deterministic_id_is_non_secret_and_stable():
    assert deterministic_snapshot_id("BTC", "fixture", "2026-09-19T12:00:00Z") == deterministic_snapshot_id("BTC", "fixture", "2026-09-19T12:00:00Z")
    assert SnapshotStatus.UNAVAILABLE.value == "unavailable"
