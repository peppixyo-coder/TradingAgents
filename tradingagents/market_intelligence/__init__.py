"""Optional, read-only external market intelligence boundaries."""

from .adapters import MassiveAdapter, OpenBBAdapter, unavailable
from .config import settings
from .context import format_external_market_context
from .contract import ExternalSnapshot, SnapshotStatus, validate_snapshot

__all__ = ["ExternalSnapshot", "SnapshotStatus", "validate_snapshot",
           "MassiveAdapter", "OpenBBAdapter", "unavailable", "settings",
           "format_external_market_context"]
