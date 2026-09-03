"""Regressione T33: throttle LLM adattivo - delay = base x worker attivi.

_active_workers() conta i thread con una chiamata negli ultimi
_ACTIVE_WINDOW_S (questo incluso) e pota gli stalli fuori finestra;
invoke() moltiplica la base per quel conteggio. Zero rete, zero mock.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.llm_clients import openai_client as O  # noqa: E402


def test_conteggio_worker_attivi():
    O._ACTIVE.clear()
    assert O._active_workers() == 1              # solo questo thread
    t = threading.Thread(target=O._active_workers)
    t.start()
    t.join()
    assert O._active_workers() == 2              # il 2o resta in finestra
    O._ACTIVE.clear()


def test_potatura_fuori_finestra():
    O._ACTIVE.clear()
    O._ACTIVE[424242] = time.monotonic() - O._ACTIVE_WINDOW_S - 1  # stallo
    assert O._active_workers() == 1              # potato e non conta
    assert 424242 not in O._ACTIVE
    O._ACTIVE.clear()


def test_finestra_registra_3_worker():
    """3 worker in finestra => delay = 3 x base (la formula di invoke)."""
    O._ACTIVE.clear()
    for i in range(2):                          # 2 fantasmi + questo = 3
        O._ACTIVE[9000 + i] = time.monotonic()
    assert O._active_workers() == 3
    O._ACTIVE.clear()


if __name__ == "__main__":
    test_conteggio_worker_attivi()
    test_potatura_fuori_finestra()
    test_finestra_registra_3_worker()
    print("OK: 3/3 check passati")
