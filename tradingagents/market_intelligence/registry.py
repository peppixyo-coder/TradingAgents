"""Read-only snapshot registry backed by an optional JSON export path."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import settings
from .contract import validate_snapshot


def load_snapshots(path: str | None = None) -> list[dict[str, Any]]:
    """Load bounded snapshots from an explicit export; never writes state."""
    source = path or os.getenv("HL_EXTERNAL_SNAPSHOT_FILE", "")
    if not source:
        return []
    try:
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
        return [validate_snapshot(row, allow_stale=True) for row in rows]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []


def collect_snapshots(asset: str, *, hyperliquid: dict[str, Any] | None = None,
                      openbb: Any = None, massive: Any = None) -> list[dict[str, Any]]:
    """Collect advisory snapshots lazily; never runs during import/startup."""
    rows = []
    if hyperliquid is not None:
        rows.append(validate_snapshot(hyperliquid, allow_stale=True))
    cfg = settings()
    if cfg["openbb_enabled"]:
        adapter = openbb or _openbb_adapter(cfg)
        rows.append(validate_snapshot(adapter.snapshot(asset), allow_stale=True))
    if cfg["massive_enabled"]:
        adapter = massive or _massive_adapter(cfg)
        rows.append(validate_snapshot(adapter.snapshot(asset), allow_stale=True))
    return rows


def _openbb_adapter(cfg: dict[str, object]):
    from .adapters import OpenBBAdapter
    return OpenBBAdapter(enabled=True, timeout_s=float(cfg["timeout_s"]))


def _massive_adapter(cfg: dict[str, object]):
    from .adapters import MassiveAdapter
    return MassiveAdapter(enabled=True, timeout_s=float(cfg["timeout_s"]),
                          base_url=str(cfg["massive_base_url"]))


def advisory_snapshots(asset: str) -> list[dict[str, Any]]:
    """Load snapshots and invoke enabled providers only on dashboard request."""
    rows = load_snapshots()
    primary = next((row for row in rows if row["asset"] == asset
                    and row["provider"] == "hyperliquid"), None)
    external = [row for row in rows if row is not primary]
    cfg = settings()
    return external + collect_snapshots(
        asset,
        hyperliquid=primary,
        openbb=_openbb_adapter(cfg) if cfg["openbb_enabled"] else None,
        massive=_massive_adapter(cfg) if cfg["massive_enabled"] else None,
    )


def health() -> dict[str, Any]:
    cfg = settings()
    snaps = load_snapshots()
    by_provider: dict[str, dict[str, Any]] = {}
    for row in snaps:
        by_provider[row["provider"]] = {
            "status": row["status"],
            "fetched_at": row["fetched_at"],
            "freshness_seconds": row["quality"].get("freshness_seconds"),
            "source": row["quality"].get("source"),
            "errors": row["quality"].get("errors", []),
        }
    for provider, active in (("openbb", cfg["openbb_enabled"]),
                             ("massive", cfg["massive_enabled"])):
        by_provider.setdefault(provider, {
            "status": "unavailable" if not active else "not_configured",
            "fetched_at": None, "freshness_seconds": None,
            "source": provider, "errors": ["disabled" if not active else "no snapshot"]})
    return {"enabled": cfg["external_data_enabled"], "context_enabled": cfg["external_context_enabled"],
            "providers": by_provider}
