"""Read-only snapshot registry backed by an optional JSON export path."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .adapters import unavailable
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
    for provider, active in (("openbb", cfg["openbb_enabled"]), ("fincept", cfg["fincept_enabled"])):
        by_provider.setdefault(provider, {
            "status": "unavailable" if not active else "not_configured",
            "fetched_at": None, "freshness_seconds": None,
            "source": provider, "errors": ["disabled" if not active else "no snapshot"]})
    return {"enabled": cfg["external_data_enabled"], "context_enabled": cfg["external_context_enabled"],
            "providers": by_provider}
