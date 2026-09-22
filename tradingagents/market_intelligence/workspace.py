"""Versioned, bounded advisory workspace contract and isolated file store.

The store persists UI configuration only. It has no HTTP, dashboard, or
trading-state authority.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
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
_UI_FIELDS = frozenset(
    {"asset", "timeframe", "compare_assets", "search", "sort", "indicator_settings"}
)


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
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_length
        or any(ord(c) < 32 for c in value)
    ):
        raise _error("payload_invalid", f"{field} must be bounded text", field)
    return value


def _unknown(raw: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _error("unknown_field", f"unknown field: {unknown[0]}", unknown[0])


def validate_workspace_payload(
    raw: Mapping[str, Any], *, existing_names: Sequence[str] = ()
) -> dict[str, Any]:
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
    if (
        not (1 <= len(name) <= MAX_WORKSPACE_NAME)
        or name.lower() in _RESERVED_NAMES
        or not _NAME_RE.fullmatch(name)
        or ".." in name
    ):
        raise _error("name_invalid", "workspace name contains forbidden characters", "name")
    if any(
        isinstance(item, str) and item.strip().casefold() == name.casefold()
        for item in existing_names
    ):
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
        raise _error(
            "limit_exceeded",
            "compare_assets must contain at most three assets",
            "ui.compare_assets",
        )
    if any(not isinstance(item, str) or not item for item in compare) or len(set(compare)) != len(
        compare
    ):
        raise _error(
            "payload_invalid",
            "compare_assets must be unique non-empty strings",
            "ui.compare_assets",
        )
    search = ui.get("search", "")
    sort = ui.get("sort", "coin")
    if (
        not isinstance(search, str)
        or len(search) > MAX_TEXT
        or not isinstance(sort, str)
        or len(sort) > MAX_TEXT
    ):
        raise _error("payload_invalid", "search and sort must be bounded text", "ui")
    settings = ui.get("indicator_settings", {})
    if not isinstance(settings, Mapping) or settings:
        raise _error(
            "payload_invalid",
            "indicator_settings must be an empty object in schema v1",
            "ui.indicator_settings",
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "ui": {
            "asset": asset,
            "timeframe": timeframe,
            "compare_assets": list(compare),
            "search": search,
            "sort": sort,
            "indicator_settings": {},
        },
    }


def check_workspace_compatibility(
    workspace: Mapping[str, Any], watchlist: Sequence[str]
) -> dict[str, Any]:
    """Report missing assets without mutating or replacing workspace values."""
    allowed = {item for item in watchlist if isinstance(item, str)}
    selected = [workspace["ui"]["asset"], *workspace["ui"]["compare_assets"]]
    missing = sorted({item for item in selected if item not in allowed})
    return {"compatible": not missing, "missing_assets": missing}


MAX_WORKSPACES = 32
MAX_STORE_BYTES = 512_000


class WorkspaceStoreError(ValueError):
    """Deterministic storage error suitable for a future HTTP adapter."""

    def __init__(self, code: str, message: str, fields: Mapping[str, str] | None = None):
        super().__init__(message)
        self.code, self.message, self.fields = code, message, dict(fields or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "fields": self.fields}


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root(root: str | os.PathLike[str]) -> Path:
    path = Path(root)
    if not path.is_absolute():
        raise WorkspaceStoreError("workspace_storage_unavailable", "storage root must be absolute")
    resolved = path.resolve()
    if (
        "state" in {part.casefold() for part in resolved.parts}
        or "cache" in {part.casefold() for part in resolved.parts}
        or resolved.name.casefold() == "bot.db"
    ):
        raise WorkspaceStoreError("workspace_storage_unavailable", "storage root is operational")
    return resolved


class WorkspaceStore:
    """Per-installation JSON store; no HTTP or trading-state integration."""

    def __init__(self, root: str | os.PathLike[str], *, max_total_bytes: int = MAX_STORE_BYTES):
        self.root = _root(root)
        self.max_total_bytes = max_total_bytes
        self._lock_key = str(self.root).casefold()
        with _LOCKS_GUARD:
            _LOCKS.setdefault(self._lock_key, threading.RLock())

    @property
    def _lock(self) -> threading.RLock:
        return _LOCKS[self._lock_key]

    @contextmanager
    def _cross_process_lock(self, timeout: float = 2.0):
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / ".workspace.lock"
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while time.monotonic() < deadline:
                try:
                    os.mkdir(marker)
                    acquired = True
                    break
                except FileExistsError:
                    time.sleep(0.02)
            if not acquired:
                raise WorkspaceStoreError("workspace_storage_unavailable", "workspace lock timeout")
            yield
        finally:
            if acquired:
                with contextlib.suppress(OSError):
                    marker.rmdir()

    def _files(self) -> list[Path]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise WorkspaceStoreError(
                "workspace_storage_unavailable", "storage root is not a directory"
            )
        return sorted(self.root.glob("*.json"))

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("payload"), dict):
                raise ValueError
            return raw
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceStoreError("workspace_corrupt", "workspace record is corrupt") from exc

    def _records(self) -> list[dict[str, Any]]:
        return [self._read(path) for path in self._files()]

    def _get_unlocked(self, workspace_id: str) -> dict[str, Any]:
        path = self.root / f"{workspace_id}.json"
        if not path.is_file():
            raise WorkspaceStoreError("workspace_not_found", "workspace not found")
        return self._read(path)

    def _write(self, record: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{record['workspace_id']}.json"
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{record['workspace_id']}.", suffix=".tmp", dir=self.root
        )
        try:
            data = json.dumps(
                record, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except (OSError, TypeError, ValueError) as exc:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise WorkspaceStoreError(
                "workspace_storage_unavailable", "workspace write failed"
            ) from exc

    def _check_total(
        self, replacement: dict[str, Any] | None = None, replacing_id: str | None = None
    ) -> None:
        total = sum(
            path.stat().st_size
            for path in self._files()
            if not replacing_id or path.stem != replacing_id
        )
        if replacement is not None:
            total += len(
                json.dumps(
                    replacement, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            )
        if total > self.max_total_bytes:
            raise WorkspaceStoreError(
                "workspace_limit_exceeded", "workspace store exceeds maximum size"
            )

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock, self._cross_process_lock():
            records = self._records()
            try:
                clean = validate_workspace_payload(
                    payload, existing_names=[r.get("payload", {}).get("name") for r in records]
                )
            except WorkspaceValidationError as exc:
                code = "workspace_duplicate" if exc.code == "duplicate" else "workspace_invalid"
                raise WorkspaceStoreError(code, exc.message, exc.fields) from exc
            if len(records) >= MAX_WORKSPACES:
                raise WorkspaceStoreError("workspace_limit_exceeded", "workspace count exceeded")
            timestamp = _now()
            record = {
                "schema_version": SCHEMA_VERSION,
                "workspace_id": str(uuid.uuid4()),
                "revision": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
                "payload": clean,
            }
            self._check_total(record)
            self._write(record)
            return record

    def get(self, workspace_id: str) -> dict[str, Any]:
        with self._lock, self._cross_process_lock():
            return self._get_unlocked(workspace_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._cross_process_lock():
            return self._records()

    def update(
        self, workspace_id: str, expected_revision: int, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock, self._cross_process_lock():
            current = self._get_unlocked(workspace_id)
            if current.get("revision") != expected_revision:
                raise WorkspaceStoreError("workspace_conflict", "workspace revision conflict")
            names = [
                r.get("payload", {}).get("name")
                for r in self._records()
                if r.get("workspace_id") != workspace_id
            ]
            try:
                clean = validate_workspace_payload(payload, existing_names=names)
            except WorkspaceValidationError as exc:
                code = "workspace_duplicate" if exc.code == "duplicate" else "workspace_invalid"
                raise WorkspaceStoreError(code, exc.message, exc.fields) from exc
            record = {
                **current,
                "revision": expected_revision + 1,
                "updated_at": _now(),
                "payload": clean,
            }
            self._check_total(record, workspace_id)
            self._write(record)
            return record

    def delete(self, workspace_id: str, expected_revision: int) -> None:
        with self._lock, self._cross_process_lock():
            current = self._get_unlocked(workspace_id)
            if current.get("revision") != expected_revision:
                raise WorkspaceStoreError("workspace_conflict", "workspace revision conflict")
            try:
                (self.root / f"{workspace_id}.json").unlink()
            except OSError as exc:
                raise WorkspaceStoreError(
                    "workspace_storage_unavailable", "workspace delete failed"
                ) from exc
