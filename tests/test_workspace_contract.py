import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from tradingagents.market_intelligence.workspace import (  # noqa: E402
    MAX_WORKSPACE_BYTES,
    WorkspaceValidationError,
    check_workspace_compatibility,
    validate_workspace_payload,
)


def payload(**overrides):
    value = {
        "schema_version": 1,
        "name": "BTC intraday",
        "ui": {
            "asset": "BTC",
            "timeframe": "1h",
            "compare_assets": ["BTC", "ETH"],
            "search": "",
            "sort": "coin",
            "indicator_settings": {},
        },
    }
    value.update(overrides)
    return value


def test_valid_payload_is_normalized_without_server_metadata():
    out = validate_workspace_payload(payload())
    assert out == payload()
    assert "workspace_id" not in out and "revision" not in out


def test_name_is_trimmed_and_bounded():
    assert validate_workspace_payload(payload(name="  desk  "))["name"] == "desk"
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload(payload(name="x" * 65))
    assert exc.value.code == "name_invalid"


@pytest.mark.parametrize("name", ["../desk", "a/b", r"a\\b", ".", "..", "default", "state", "bot", "system", "desk\u200b"])
def test_name_rejects_paths_reserved_and_invisible_values(name):
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload(payload(name=name))
    assert exc.value.code == "name_invalid"


def test_unknown_fields_and_wrong_schema_are_rejected():
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload({**payload(), "workspace_id": "server-only"})
    assert exc.value.code == "unknown_field"
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload(payload(schema_version=2))
    assert exc.value.code == "schema_unsupported"


def test_timeframe_compare_duplicates_and_nulls_are_rejected():
    for ui in (
        {**payload()["ui"], "timeframe": "5m"},
        {**payload()["ui"], "compare_assets": ["BTC", "ETH", "SOL", "X"]},
        {**payload()["ui"], "compare_assets": ["BTC", "BTC"]},
        {**payload()["ui"], "asset": None},
        {**payload()["ui"], "indicator_settings": {"period": None}},
    ):
        with pytest.raises(WorkspaceValidationError):
            validate_workspace_payload({**payload(), "ui": ui})



def test_duplicate_name_is_rejected_case_insensitively():
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload(payload(name="Desk"), existing_names=[" desk "])
    assert exc.value.code == "duplicate"

def test_nonfinite_and_oversized_payloads_are_rejected():
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload({**payload(), "ui": {**payload()["ui"], "search": math.nan}})
    assert exc.value.code == "payload_invalid"
    with pytest.raises(WorkspaceValidationError) as exc:
        validate_workspace_payload({**payload(), "name": "x" * 64, "ui": {**payload()["ui"], "search": "x" * MAX_WORKSPACE_BYTES}})
    assert exc.value.code == "limit_exceeded"


def test_missing_watchlist_asset_is_incompatible_not_replaced():
    out = validate_workspace_payload(payload())
    compatibility = check_workspace_compatibility(out, ["SOL"])
    assert compatibility == {"compatible": False, "missing_assets": ["BTC", "ETH"]}
    assert out["ui"]["asset"] == "BTC"


def test_error_is_deterministically_serializable():
    error = WorkspaceValidationError("name_invalid", "invalid workspace name", {"name": "bad"})
    assert error.to_dict() == {"code": "name_invalid", "message": "invalid workspace name", "fields": {"name": "bad"}}
