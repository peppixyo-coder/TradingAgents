"""Optional, read-only external market intelligence boundaries."""

from .adapters import FinceptAdapter, OpenBBAdapter, unavailable
from .contract import ExternalSnapshot, SnapshotStatus, validate_snapshot

__all__ = ["ExternalSnapshot", "SnapshotStatus", "validate_snapshot",
           "OpenBBAdapter", "FinceptAdapter", "unavailable"]
