"""Regressione: la dashboard serve dati completi su REST, WS e HTML.

Wrapper sullo script scripts/dashboard_healthcheck.py (stessa fonte di verita').
Richiede lo stack attivo (docker compose up dashboard); fallisce se giu'.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dashboard_healthcheck as hc  # noqa: E402


def test_dashboard_serves_complete_data():
    checks = []
    hc.check_rest(checks)
    hc.check_ws(checks)
    hc.check_html(checks)
    failed = [(n, v) for n, ok, v in checks if not ok]
    assert not failed, (
        f"Dashboard rotta ({len(failed)}/{len(checks)} check falliti):\n"
        + "\n".join(f"  x {n} = {v}" for n, v in failed))
