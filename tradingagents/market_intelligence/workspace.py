"""Versioned, bounded advisory workspace contract.

This module validates UI configuration only. It has no storage, HTTP, dashboard,
or trading-state authority.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1
MAX_WORKSPACE_BYTES = 32_768
MAX_WORKSPACE_NAME = 64
MAX_TEXT = 256
MAX_COMPARE_ASSETS = 3
SUPPORTED_TIMEFRAMES = frozenset({"1h", "4h", "1d"})
_RESERVED_NAMES = frozenset({".", "..", "default", "state", "bot", "system"})
_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")
_ROOT_FIELDS = frozenset({"schema_version", "name", "ui"})
_UI_FIELDS = frozenset({"asset", "timeframe", "compare_assets", "search", "sort", "indicator_settings"})


class WorkspaceValidationError(ValueError):
    """Deterministic validation error suitable for a future HTTP adapter."""

    def __init__(self, code: str, message: str, fields: Mapping[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = dict(fields or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "fields": self.fields}


def _error(code: str, message: str, field: str | None = None) -> WorkspaceValidationError:
    return WorkspaceValidationError(code, message, {field: message} if field else None)


def _check_finite(value: Any, path: str = "payload") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise _error("payload_invalid", "number must be finite", path)
    if isinstance(value, Mapping):
        for key, item in value.items():
            _check_finite(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _check_finite(item, f"{path}[{index}]")


def _text(value: Any, field: str, *, max_length: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or any(ord(c) < 32 for c in value):
        raise _error("payload_invalid", f"{field} must be bounded text", field)
    return value


def _unknown(raw: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _error("unknown_field", f"unknown field: {unknown[0]}", unknown[0])


def validate_workspace_payload(raw: Mapping[str, Any], *, existing_names: Sequence[str] = ()) -> dict[str, Any]:
    """Validate and return a normalized client workspace payload."""
    if not isinstance(raw, Mapping):
        raise _error("payload_invalid", "workspace payload must be an object")
    try:
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _error("payload_invalid", "workspace payload must be JSON-safe") from exc
    if len(encoded.encode("utf-8")) > MAX_WORKSPACE_BYTES:
        raise _error("limit_exceeded", "workspace payload exceeds maximum size")
    _check_finite(raw)
    _unknown(raw, _ROOT_FIELDS)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise _error("schema_unsupported", "unsupported workspace schema", "schema_version")
    name = raw.get("name")
    if not isinstance(name, str):
        raise _error("name_invalid", "workspace name must be text", "name")
    name = name.strip()
    if not (1 <= len(name) <= MAX_WORKSPACE_NAME) or name.lower() in _RESERVED_NAMES or not _NAME_RE.fullmatch(name) or ".." in name:
        raise _error("name_invalid", "workspace name contains forbidden characters", "name")
    if any(isinstance(item, str) and item.strip().casefold() == name.casefold() for item in existing_names):
        raise _error("duplicate", "workspace name already exists", "name")
    ui = raw.get("ui")
    if not isinstance(ui, Mapping):
        raise _error("payload_invalid", "ui must be an object", "ui")
    _unknown(ui, _UI_FIELDS)
    asset = _text(ui.get("asset"), "ui.asset")
    timeframe = ui.get("timeframe")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise _error("payload_invalid", "unsupported timeframe", "ui.timeframe")
    compare = ui.get("compare_assets", [])
    if not isinstance(compare, list) or len(compare) > MAX_COMPARE_ASSETS:
        raise _error("limit_exceeded", "compare_assets must contain at most three assets", "ui.compare_assets")
    if any(not isinstance(item, str) or not item for item in compare) or len(set(compare)) != len(compare):
        raise _error("payload_invalid", "compare_assets must be unique non-empty strings", "ui.compare_assets")
    search = ui.get("search", "")
    sort = ui.get("sort", "coin")
    if not isinstance(search, str) or len(search) > MAX_TEXT or not isinstance(sort, str) or len(sort) > MAX_TEXT:
        raise _error("payload_invalid", "search and sort must be bounded text", "ui")
    settings = ui.get("indicator_settings", {})
    if not isinstance(settings, Mapping) or settings:
        raise _error("payload_invalid", "indicator_settings must be an empty object in schema v1", "ui.indicator_settings")
    return {"schema_version": SCHEMA_VERSION, "name": name, "ui": {"asset": asset, "timeframe": timeframe, "compare_assets": list(compare), "search": search, "sort": sort, "indicator_settings": {}}}


def check_workspace_compatibility(workspace: Mapping[str, Any], watchlist: Sequence[str]) -> dict[str, Any]:
    """Report missing assets without mutating or replacing workspace values."""
    allowed = {item for item in watchlist if isinstance(item, str)}
    selected = [workspace["ui"]["asset"], *workspace["ui"]["compare_assets"]]
    missing = sorted({item for item in selected if item not in allowed})
    return {"compatible": not missing, "missing_assets": missing}
