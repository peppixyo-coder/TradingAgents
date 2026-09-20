"""Versioned, bounded read-only contract for external market data.

External responses are untrusted data. This module validates shape, bounds text,
redacts credential-shaped values, and never interprets content as instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 64_000
MAX_TEXT = 2_000
MAX_ITEMS = 100
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|private[_-]?key)")


class SnapshotStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    STALE = "stale"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    NOT_CONFIGURED = "not configured"

@dataclass(frozen=True)
class ExternalSnapshot:
    schema_version: int
    snapshot_id: str
    asset: str
    canonical_asset: str
    fetched_at: str
    as_of: str | None
    provider: str
    status: SnapshotStatus
    data: Mapping[str, Any] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return validate_snapshot(asdict(self), allow_stale=True)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _utc(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO-8601 text")
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ValueError("snapshot data nesting exceeds limit")
    if isinstance(value, Mapping):
        if len(value) > MAX_ITEMS:
            raise ValueError("snapshot object has too many fields")
        return {str(k)[:80]: _safe(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ITEMS:
            raise ValueError("snapshot list exceeds limit")
        return [_safe(v, depth=depth + 1) for v in value]
    if isinstance(value, str):
        if _SECRET.search(value):
            return "[REDACTED]"
        return value[:MAX_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError(f"unsupported snapshot value: {type(value).__name__}")


def validate_snapshot(raw: Mapping[str, Any], *, allow_stale: bool = False) -> dict[str, Any]:
    """Validate and return a bounded JSON-safe snapshot copy."""
    if not isinstance(raw, Mapping):
        raise ValueError("snapshot must be an object")
    required = {"schema_version", "snapshot_id", "asset", "canonical_asset",
                "fetched_at", "as_of", "provider", "status", "data",
                "quality", "provenance"}
    missing = required - set(raw)
    if missing:
        raise ValueError(f"snapshot missing fields: {sorted(missing)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported snapshot schema_version")
    asset = raw["asset"]
    canonical = raw["canonical_asset"]
    provider = raw["provider"]
    if not isinstance(asset, str) or not asset.strip() or len(asset) > 128:
        raise ValueError("asset must be bounded text")
    if not isinstance(canonical, str) or not canonical.strip() or len(canonical) > 128:
        raise ValueError("canonical_asset must be bounded text")
    if not isinstance(provider, str) or not provider.strip() or len(provider) > 64:
        raise ValueError("provider must be bounded text")
    status = SnapshotStatus(raw["status"])
    fetched = _utc(raw["fetched_at"])
    as_of = _utc(raw["as_of"], allow_none=True)
    quality = _safe(raw["quality"])
    freshness = quality.get("freshness_seconds")
    if freshness is not None and (not isinstance(freshness, (int, float)) or freshness < 0):
        raise ValueError("freshness_seconds must be a non-negative number")
    if status == SnapshotStatus.STALE and not allow_stale:
        raise ValueError("stale snapshot requires allow_stale=True")
    if status == SnapshotStatus.UNSUPPORTED and quality.get("coverage") != "none":
        raise ValueError("unsupported snapshot must have no coverage")
    result = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": str(raw["snapshot_id"])[:128],
        "asset": asset.strip(),
        "canonical_asset": str(raw["canonical_asset"])[:128],
        "fetched_at": fetched,
        "as_of": as_of,
        "provider": provider.strip(),
        "status": status.value,
        "data": _safe(raw["data"]),
        "quality": quality,
        "provenance": _safe(raw["provenance"]),
    }
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds maximum serialized size")
    return result


def deterministic_snapshot_id(asset: str, provider: str, fetched_at: str) -> str:
    """Create a non-secret stable ID for fixture/cache correlation."""
    return hashlib.sha256(f"{provider}:{asset}:{fetched_at}".encode()).hexdigest()[:24]
