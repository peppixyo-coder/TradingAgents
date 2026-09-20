"""Read-only precedence and fallback aggregation for advisory/dashboard data."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contract import SnapshotStatus, validate_snapshot

# Only mappings explicitly verified by a caller belong here. Empty by default.
MASSIVE_SYMBOL_MAP: dict[str, str] = {}


def massive_symbol(asset: str) -> str | None:
    return MASSIVE_SYMBOL_MAP.get(asset)


def aggregate_snapshot(
    asset: str,
    hyperliquid: Mapping[str, Any] | None,
    external: list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Select HL when usable; expose external fallback reason otherwise."""
    primary = validate_snapshot(hyperliquid, allow_stale=True) if hyperliquid else None
    if primary and primary["status"] not in {SnapshotStatus.STALE.value,
                                              SnapshotStatus.UNAVAILABLE.value,
                                              SnapshotStatus.ERROR.value,
                                              SnapshotStatus.UNSUPPORTED.value}:
        return {"snapshot": primary, "primary": True, "fallback_reason": None}
    for raw in external:
        candidate = validate_snapshot(raw, allow_stale=True)
        if candidate["asset"] == asset and candidate["status"] == SnapshotStatus.OK.value:
            return {"snapshot": candidate, "primary": False,
                    "fallback_reason": primary["status"] if primary else "missing"}
    if primary:
        return {"snapshot": primary, "primary": True,
                "fallback_reason": "no usable external fallback"}
    return {"snapshot": None, "primary": False, "fallback_reason": "all providers unavailable"}
