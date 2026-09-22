"""Hyperliquid runtime configuration regressions."""

import pytest

from tradingagents.hyperliquid.config import load


@pytest.mark.unit
def test_asset_blacklist_reads_process_environment(monkeypatch):
    monkeypatch.setenv("HL_ASSET_BLACKLIST", " xyz:SKHX,xyz:CL,, xyz:DRAM ")
    cfg = load()
    assert cfg.asset_blacklist == ("xyz:SKHX", "xyz:CL", "xyz:DRAM")
