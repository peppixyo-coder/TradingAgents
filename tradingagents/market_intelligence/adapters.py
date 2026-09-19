"""Optional read-only adapters; no trading or operational-state authority."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .contract import ExternalSnapshot, SnapshotStatus, deterministic_snapshot_id, validate_snapshot


class AdapterError(RuntimeError):
    """Bounded adapter failure safe to surface as unavailable data."""


def unavailable(asset: str, provider: str, reason: str) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return validate_snapshot({
        "schema_version": 1,
        "snapshot_id": deterministic_snapshot_id(asset, provider, now),
        "asset": asset,
        "canonical_asset": asset,
        "fetched_at": now,
        "as_of": None,
        "provider": provider,
        "status": SnapshotStatus.UNAVAILABLE.value,
        "data": {},
        "quality": {"freshness_seconds": None, "source": provider,
                    "coverage": "none", "errors": [reason[:500]]},
        "provenance": {"endpoint_or_query": "redacted", "license": "unknown",
                        "requires_api_key": False, "paid": False},
    })


class OpenBBAdapter:
    """Lazy OpenBB boundary. Disabled unless explicitly enabled by caller."""

    provider = "openbb"

    def __init__(self, *, enabled: bool = False, timeout_s: float = 5.0):
        self.enabled = enabled
        self.timeout_s = max(0.1, min(float(timeout_s), 30.0))

    def snapshot(self, asset: str, fetcher: Callable[[str], Mapping[str, Any]] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return unavailable(asset, self.provider, "not configured")
        try:
            if fetcher is None:
                __import__("openbb")
                return unavailable(asset, self.provider, "provider query not configured")
            started = time.monotonic()
            data = fetcher(asset)
            if time.monotonic() - started > self.timeout_s:
                return unavailable(asset, self.provider, "timeout")
            if not isinstance(data, Mapping):
                return unavailable(asset, self.provider, "malformed response")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return validate_snapshot({
                "schema_version": 1,
                "snapshot_id": deterministic_snapshot_id(asset, self.provider, now),
                "asset": asset, "canonical_asset": asset, "fetched_at": now,
                "as_of": data.get("as_of"), "provider": self.provider,
                "status": SnapshotStatus.OK.value, "data": data,
                "quality": {"freshness_seconds": 0, "source": "openbb",
                            "coverage": "full", "errors": []},
                "provenance": {"endpoint_or_query": "redacted", "license": "AGPL-3.0",
                                "requires_api_key": True, "paid": False},
            })
        except Exception as exc:  # provider boundary must not crash caller
            return unavailable(asset, self.provider, type(exc).__name__)


class FinceptAdapter:
    """Disabled-by-default boundary for documented local JSON/CSV exports only."""

    provider = "fincept"

    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled

    def import_file(self, path: str | Path, asset: str) -> dict[str, Any]:
        if not self.enabled:
            return unavailable(asset, self.provider, "bridge unavailable/not configured")
        source = Path(path)
        try:
            if source.suffix.lower() == ".json":
                data = json.loads(source.read_text(encoding="utf-8"))
            elif source.suffix.lower() == ".csv":
                with source.open(newline="", encoding="utf-8") as fh:
                    data = list(csv.DictReader(fh))
            else:
                return unavailable(asset, self.provider, "only documented JSON/CSV imports allowed")
            if not isinstance(data, (Mapping, list)):
                return unavailable(asset, self.provider, "malformed export")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return validate_snapshot({
                "schema_version": 1,
                "snapshot_id": deterministic_snapshot_id(asset, self.provider, now),
                "asset": asset, "canonical_asset": asset, "fetched_at": now,
                "as_of": None, "provider": self.provider,
                "status": SnapshotStatus.OK.value, "data": data,
                "quality": {"freshness_seconds": 0, "source": "documented_export",
                            "coverage": "partial", "errors": []},
                "provenance": {"endpoint_or_query": "local export filename only",
                                "license": "Fincept terms apply to source",
                                "requires_api_key": False, "paid": False},
            })
        except Exception as exc:
            return unavailable(asset, self.provider, type(exc).__name__)
