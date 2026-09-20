"""Optional read-only adapters; no trading or operational-state authority."""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .config import massive_api_key
from .contract import (
    MAX_SNAPSHOT_BYTES,
    SnapshotStatus,
    deterministic_snapshot_id,
    validate_snapshot,
)


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

def _status_snapshot(asset: str, status: SnapshotStatus, reason: str) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return validate_snapshot({"schema_version": 1,
        "snapshot_id": deterministic_snapshot_id(asset, "openbb", status.value),
        "asset": asset, "canonical_asset": asset, "fetched_at": now, "as_of": None,
        "provider": "openbb", "status": status.value, "data": {},
        "quality": {"freshness_seconds": None, "source": "openbb", "coverage": "none", "errors": [reason]},
        "provenance": {"endpoint_or_query": "crypto.price.historical redacted",
                        "license": "AGPL-3.0", "requires_api_key": True, "paid": False}})

class OpenBBAdapter:
    """Lazy OpenBB crypto historical adapter; network is only caller-triggered."""

    provider = "openbb"
    SUPPORTED_ASSETS: dict[str, str] = {}

    def __init__(self, *, enabled: bool = False, provider_name: str = "yfinance",
                 timeout_s: float = 5.0):
        self.enabled = enabled
        self.provider_name = provider_name
        self.timeout_s = max(0.1, min(float(timeout_s), 30.0))

    def snapshot(self, asset: str, *, client: Any = None,
                 start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return unavailable(asset, self.provider, "not configured")
        symbol = self.SUPPORTED_ASSETS.get(asset)
        if not symbol:
            return _status_snapshot(asset, SnapshotStatus.UNSUPPORTED, "verified asset mapping required")
        try:
            if client is None:
                from openbb import obb  # lazy: no import at module/startup time
                client = obb
            started = time.monotonic()
            result = client.crypto.price.historical(symbol=symbol, provider=self.provider_name,
                                                    start_date=start_date, end_date=end_date)
            if time.monotonic() - started > self.timeout_s:
                return unavailable(asset, self.provider, "timeout")
            frame = result.to_df() if hasattr(result, "to_df") else result
            if frame is None or getattr(frame, "empty", False):
                return unavailable(asset, self.provider, "empty response")
            rows = frame.reset_index().to_dict(orient="records") if hasattr(frame, "reset_index") else frame
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return validate_snapshot({"schema_version": 1,
                "snapshot_id": deterministic_snapshot_id(asset, self.provider, now),
                "asset": asset, "canonical_asset": asset, "fetched_at": now,
                "as_of": None, "provider": self.provider,
                "status": SnapshotStatus.OK.value,
                "data": {"symbol": symbol, "rows": rows},
                "quality": {"freshness_seconds": 0, "source": f"openbb:{self.provider_name}",
                            "coverage": "full", "errors": []},
                "provenance": {"endpoint_or_query": "crypto.price.historical redacted",
                                "license": "AGPL-3.0", "requires_api_key": self.provider_name != "yfinance", "paid": False}})
        except ImportError:
            return unavailable(asset, self.provider, "openbb package unavailable")
        except Exception as exc:
            return unavailable(asset, self.provider, type(exc).__name__)

class MassiveAdapter:
    """Lazy, read-only Massive crypto snapshot boundary.

    ``SUPPORTED_ASSETS`` is deliberately empty until a mapping is verified.
    The optional fetcher exists only for offline fixtures and receives the
    documented ticker; the real transport is created inside ``snapshot``.
    """

    provider = "massive"
    SUPPORTED_ASSETS: dict[str, str] = {}
    endpoint_template = "/v2/snapshot/locale/global/markets/crypto/tickers/{ticker}"

    def __init__(self, *, enabled: bool = False, timeout_s: float = 5.0,
                 base_url: str = "https://api.massive.com"):
        self.enabled = enabled
        self.timeout_s = max(0.1, min(float(timeout_s), 30.0))
        self.base_url = base_url.rstrip("/")

    def _snapshot(self, asset: str, status: SnapshotStatus, reason: str,
                  *, data: Mapping[str, Any] | None = None,
                  symbol: str | None = None, as_of: str | None = None,
                  freshness: float | None = None) -> dict[str, Any]:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return validate_snapshot({
            "schema_version": 1,
            "snapshot_id": deterministic_snapshot_id(asset, self.provider, now),
            "asset": asset, "canonical_asset": asset,
            "fetched_at": now, "as_of": as_of, "provider": self.provider,
            "status": status.value, "data": dict(data or {}),
            "quality": {"freshness_seconds": freshness, "source": self.provider,
                        "coverage": "partial" if data else "none",
                        "errors": [reason[:500]] if reason else []},
            "provenance": {
                "endpoint_or_query": self._provenance(symbol),
                "license": "Massive terms", "requires_api_key": True, "paid": True,
            },
        }, allow_stale=True)

    def _provenance(self, symbol: str | None) -> str:
        path = self.endpoint_template.format(ticker=quote(symbol or "", safe=""))
        return f"GET {self.base_url}{path} (query credentials redacted)"

    def _fetch(self, symbol: str) -> Mapping[str, Any]:
        key = massive_api_key()
        query = urlencode({"apiKey": key})
        url = f"{self.base_url}{self.endpoint_template.format(ticker=quote(symbol, safe=''))}?{query}"
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=self.timeout_s) as response:
            raw = response.read(64_001)
        if len(raw) > 64_000:
            raise ValueError("response exceeds maximum size")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("malformed response")
        return payload

    def snapshot(self, asset: str, *, symbol: str | None = None,
                 fetcher: Callable[[str], Mapping[str, Any]] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return self._snapshot(asset, SnapshotStatus.UNAVAILABLE, "disabled")
        resolved = symbol or self.SUPPORTED_ASSETS.get(asset)
        if not resolved:
            return self._snapshot(asset, SnapshotStatus.UNSUPPORTED,
                                  "verified symbol mapping required")
        if fetcher is None and not massive_api_key():
            return self._snapshot(asset, SnapshotStatus.NOT_CONFIGURED,
                                  "MASSIVE_API_KEY not configured", symbol=resolved)
        try:
            payload = fetcher(resolved) if fetcher else self._fetch(resolved)
            if len(json.dumps(payload, separators=(",", ":"), default=str).encode()) > MAX_SNAPSHOT_BYTES:
                raise ValueError("response exceeds maximum size")
            ticker = payload.get("ticker") if isinstance(payload, Mapping) else None
            if not isinstance(ticker, Mapping) or not ticker:
                return self._snapshot(asset, SnapshotStatus.UNAVAILABLE,
                                      "empty or malformed response", symbol=resolved)
            updated = ticker.get("updated")
            if not isinstance(updated, (int, float)) or updated <= 0:
                return self._snapshot(asset, SnapshotStatus.ERROR,
                                      "timestamp missing or invalid", symbol=resolved)
            as_of = datetime.fromtimestamp(updated / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
            freshness = max(0.0, time.time() - updated / 1000)
            status = SnapshotStatus.STALE if freshness > self.timeout_s * 60 else SnapshotStatus.OK
            return self._snapshot(asset, status, "stale snapshot" if status is SnapshotStatus.STALE else "",
                                  data={"symbol": ticker.get("ticker", resolved), "ticker": dict(ticker)},
                                  symbol=resolved, as_of=as_of, freshness=freshness)
        except HTTPError as exc:
            status = SnapshotStatus.UNAVAILABLE if exc.code in {401, 403, 429, 503} else SnapshotStatus.ERROR
            return self._snapshot(asset, status, f"HTTP {exc.code}", symbol=resolved)
        except (TimeoutError, URLError, OSError):
            return self._snapshot(asset, SnapshotStatus.UNAVAILABLE, "network or timeout", symbol=resolved)
        except (ValueError, json.JSONDecodeError, TypeError):
            return self._snapshot(asset, SnapshotStatus.ERROR, "malformed response", symbol=resolved)

