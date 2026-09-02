"""Regressioni fix E: parser JSON robusto a 3 livelli (analysts.py).

Livello 1 (_post):  body senza JSON -> RuntimeError chiaro, non ValueError criptico
Livello 2 (_chat):  risposta {"error":...} senza choices -> "" (degrada, non crash)
Livello 3 (_parse_decision): scanner bilanciato — prosa, fences, graffe in stringa.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import analysts as A  # noqa: E402


# ---- Livello 1: _post con corpo non-JSON -------------------------------------
def test_post_body_senza_json():
    import urllib.request
    from unittest.mock import patch

    class _Resp:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _fake_urlopen(req, timeout=None):
        return _Resp("data: event-stream senza json")  # nessuna '{' nel body

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        try:
            A._post("http://x", "k", {}, retries=0)
        except RuntimeError as e:
            assert "senza JSON" in str(e)
        else:
            raise AssertionError("body senza JSON deve alzare RuntimeError")


# ---- Livello 2: _chat degrada su risposta router senza choices ---------------
def test_chat_senza_choices_restituisce_vuoto():
    from types import SimpleNamespace

    def _fake_post(url, key, payload, timeout=180, retries=2):
        return {"error": {"message": "model overloaded"}}

    orig = A._post
    A._post = _fake_post  # monkeypatch modulo
    try:
        out = A._chat(SimpleNamespace(router_url="http://x", api_key="k",
                                      quick_model="Q", deep_model="D"), "sys", "user")
        assert out == "", "risposta senza choices deve dare '' (degrada)"
    finally:
        A._post = orig  # ripristina la funzione reale del modulo


# ---- Livello 3: scanner bilanciato -------------------------------------------
def test_parse_decision_pura():
    j = A._parse_decision('{"side":"long","confidence":0.8,"leverage":2}')
    assert j == {"side": "long", "confidence": 0.8, "leverage": 2}


def test_parse_decision_prosa_attorno():
    raw = ('Ecco la mia decisione:\n```json\n'
           '{"side":"short","confidence":0.6,"leverage":1.5}\n```\nFine.')
    j = A._parse_decision(raw)
    assert j and j["side"] == "short" and j["leverage"] == 1.5


def test_parse_decision_graffe_in_stringa():
    raw = ('Analisi: {"note": "setup {interessante} da tenere"} '
           '{"side":"long","confidence":0.7,"leverage":2.5}')
    j = A._parse_decision(raw)
    assert j and j["side"] == "long", "graffe in stringa non devono spezzare il parse"


def test_parse_decision_doppio_oggetto_malformato():
    # due oggetti incollati: il primo non ha side -> si prende il secondo
    raw = '{"confidence":0.9}{"side":"flat"}'
    j = A._parse_decision(raw)
    assert j and j["side"] == "flat"


def test_parse_decision_solo_spurio():
    assert A._parse_decision("nessun json qui { incompleto") is None
    assert A._parse_decision("") is None
    assert A._parse_decision(None) is None


def test_run_graph_degrada_a_flat_su_router_giu():
    """Router irraggiungibile: run_graph deve ritornare flat, non lanciare."""
    from types import SimpleNamespace

    def _post_bomb(url, key, payload, timeout=180, retries=2):
        raise RuntimeError("LLM HTTP fallita dopo 3 tentativi")

    orig = A._post
    A._post = _post_bomb
    try:
        cfg = SimpleNamespace(router_url="http://x", api_key="k",
                               quick_model="Q", deep_model="D")
        out = A.run_graph(cfg, "ctx di test")
        assert out["decision"]["side"] == "flat"
        assert "non-JSON" in out["decision"]["rationale"]
    finally:
        A._post = orig


def test_chat_split_quick_deep():
    """Split modelli: pannello+dibattito usano quick_model, decisione deep_model."""
    from types import SimpleNamespace

    seen = []

    def _post_rec(url, key, payload, timeout=180, retries=2):
        seen.append(payload["model"])
        return {"choices": [{"message": {"content": '{"side":"flat"}'}}]}

    orig = A._post
    A._post = _post_rec
    try:
        cfg = SimpleNamespace(router_url="http://x", api_key="k",
                              quick_model="Q", deep_model="D")
        A.run_graph(cfg, "ctx di test")
        assert seen == ["Q", "Q", "D"]
    finally:
        A._post = orig


if __name__ == "__main__":
    test_post_body_senza_json()
    test_chat_senza_choices_restituisce_vuoto()
    test_parse_decision_pura()
    test_parse_decision_prosa_attorno()
    test_parse_decision_graffe_in_stringa()
    test_parse_decision_doppio_oggetto_malformato()
    test_parse_decision_solo_spurio()
    test_run_graph_degrada_a_flat_su_router_giu()
    test_chat_split_quick_deep()
    print("OK: 9/9 regressioni parser + split passate")
