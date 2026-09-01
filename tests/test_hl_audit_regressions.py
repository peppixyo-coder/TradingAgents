"""Regressioni audit T20: fmt_px HL-compliant, pearson sui rendimenti,
chiusura solo su conferma fill, adozione posizioni orfane, backup orario,
screener fail-closed su listing fuori registry, trailing tollera mid mancante."""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import loop, scanner, store  # noqa: E402
from tradingagents.hyperliquid import registry, screener  # noqa: E402
from tradingagents.hyperliquid.executor import HyperliquidExecutor, _fmt_px  # noqa: E402


# ---- _fmt_px: 5 cifre significative E cap 6 - szDecimals --------------------
def test_fmt_px_sigfig():
    assert _fmt_px(123456.7) == "123460", "p>=1e5 deve roundare a 5 cifre sig"


def test_fmt_px_decimals_cap():
    assert _fmt_px(65432.1) == "65432"                    # intero <= 5 cifre
    assert _fmt_px(0.123456789, 0) == "0.12346"
    assert _fmt_px(0.123456789, 5) == "0.1"               # cap 6-szDecimals


def test_fmt_px_zero_collapse_raises():
    try:
        _fmt_px(0.000000123, 6)                           # cap 0 decimali -> "0"
    except ValueError:
        pass
    else:
        raise AssertionError("prezzo collassato a 0 deve rifiutare l'ordine")


# ---- pearson sui RENDIMENTI, non sui livelli --------------------------------
def test_corr_count_on_returns():
    base = [100 * (1 + (0.01 if i % 2 else -0.005)) ** i for i in range(30)]
    twin = [0.5 * x for x in base]                        # stesso rendimento, livello dimezzato
    noise = [100 + ((i * 7) % 13) - 6 for i in range(30)]
    candles = lambda c: [{"c": v} for v in
                         (twin if c == "ALT" else noise)]
    n = scanner.correlated_open_count(base, ["ALT", "NOISE"], "BASE", candles)
    assert n == 1, "livelli diversi con rendimenti identici devono correlare"


# ---- chiusura: archivia SOLO dopo fill confermato ---------------------------
class _Cl:
    def __init__(self, szi="1"):
        self.szi = szi

    def clearinghouse_state(self, w):
        return {"assetPositions": [
            {"position": {"coin": "ETH", "szi": self.szi, "entryPx": "3000"}}]}

    def all_mids(self):
        return {"ETH": "3000"}


class _ExFail:
    def cancel_order(self, *a):
        pass

    def cancel_tp_orders(self, *a):
        return []

    def place_market(self, *a, **k):
        return {"status": "error", "error": "mirror down"}


class _ExOk(_ExFail):
    def place_market(self, *a, **k):
        return {"status": "filled", "avg_px": "2999", "filled_sz": "1"}


def test_close_only_on_fill():
    old_db = store.DB
    try:
        store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
        store.init()
        ns = SimpleNamespace(wallet="w")
        iid = store.intent_open("ETH", "long", 1.0, 3000.0, 2900.0)
        it = dict(store.intents_open()[0])
        loop.close_position(_Cl(), ns, _ExFail(), it)     # fill fallito
        assert len(store.intents_open()) == 1, "fallita => intento resta aperto"
        loop.close_position(_Cl(), ns, _ExOk(),
                            dict(store.intents_open()[0]))
        assert store.intents_open() == [], "fill confermato => archiviata"
    finally:
        store.DB = old_db


# ---- reconcile adotta posizioni orfane --------------------------------------
class _ReconClient:
    def clearinghouse_state(self, w):
        return {"assetPositions": [
            {"position": {"coin": "DOGE", "szi": "100", "entryPx": "0.1"}}]}

    def all_mids(self):
        return {"DOGE": "0.105"}

    def candles_cached(self, coin, interval, ms):
        return []                                         # atr None -> fallback mid*2%

    def _post(self, path, payload, timeout=30):
        return {"response": {"data": {"statuses": [{"resting": {"oid": 9}}]}}}


def test_reconcile_adopts_orphan():
    old_db = store.DB
    try:
        store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
        store.init()
        cfg = SimpleNamespace(wallet="w", atr_stop_mult=2.0)
        ex = HyperliquidExecutor.__new__(HyperliquidExecutor)
        ex.cfg, ex.c = cfg, _ReconClient()
        _ReconClient.asset_index = lambda self, c: (0, {"szDecimals": 5})
        loop.reconcile(_ReconClient(), cfg, ex)
        rows = store.intents_open()
        assert len(rows) == 1 and rows[0]["coin"] == "DOGE"
        assert rows[0]["stop_oid"] == 9, "orfana adottata DEVE avere stop nativo"
        assert abs(rows[0]["qty"] - 100) < 1e-9
    finally:
        store.DB = old_db


# ---- backup orario bot.db ----------------------------------------------------
def test_backup_if_due_creates_and_prunes():
    old_db = store.DB
    try:
        store.DB = os.path.join(tempfile.mkdtemp(), "sub", "bot.db")
        store.init()
        dst = store.backup_if_due(period_s=0)
        assert dst and os.path.exists(dst), "backup creato subito con period_s=0"
        bdir = os.path.dirname(dst)
        for i in range(5):                                # vecchi backup finti
            open(os.path.join(bdir, f"bot_20200101_00000{i}.db"), "w").close()
        store._last_backup_ts = 0.0
        dst2 = store.backup_if_due(period_s=0, keep=3)
        kept = sorted(os.listdir(bdir))
        assert len(kept) == 3 and os.path.basename(dst2) in kept, \
            "prune tiene i piu' recenti"
    finally:
        store.DB = old_db


# ---- Fix B (2026-09-01): KeyError coin freschi para:SOFI / io:GPRO ----------
class _ScreenerClient:
    """Mock: mids/ctxs freschi contengono para:SOFI, la cache registry no
    (listing piu' giovane della cache 1h, la condizione del bug in prod)."""

    def asset_ctxs(self):
        return ({"universe": [{"name": "BTC", "isDelisted": False},
                              {"name": "para:SOFI", "isDelisted": False}]},
                [{"dayNtlVlm": "1e9", "openInterest": "10", "funding": "0",
                  "prevDayPx": "10"}] * 2)

    def _post(self, path, body):
        if body.get("type") == "candleSnapshot":
            return [{"t": 1}]                              # listing epoch -> eta enorme
        if body.get("type") == "l2Book":
            return {"levels": [[{"px": "100"}], [{"px": "100.01"}]]}
        return []                                          # nessun dex per-dex


def test_screener_skip_coin_fuori_registry():
    old_db = store.DB
    real_universe = registry.universe
    registry.universe = lambda c: (
        {"BTC": {"asset_class": "crypto_perp", "dex": None}}, 1, 0)
    try:
        store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
        store.init()
        passed, funnel = screener.screene(_ScreenerClient(),
                                          {"BTC": "100000", "para:SOFI": "9"})
        assert [r["coin"] for r in passed] == ["BTC"], \
            "coin fuori registry: fuori, contato, ciclo vivo (era KeyError)"
        assert funnel.get("listing_freschi_fuori") == 1, \
            "il funnel deve contare i listing freschi esclusi"
    finally:
        registry.universe = real_universe
        store.DB = old_db


def test_trailing_tollera_mid_mancante():
    """mids senza il coin dell'intento aperto (delisted): skip, il thread
    monitor non muore e gli altri intenti restano manutenuti."""
    old_db = store.DB
    try:
        store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
        store.init()
        iid = store.intent_open("XYZ:COIN", "long", 1.0, 100.0, 90.0)
        store.intent_set_peak(iid, 110.0)
        cl = type("Cl", (), {
            "clearinghouse_state": lambda s, w: {"assetPositions": [
                {"position": {"coin": "XYZ:COIN", "szi": "1"}}]},
            "all_mids": lambda s: {"BTC": "100"},          # XYZ:COIN senza mid
            "candles_cached": lambda s, *a: [],
        })()
        moved = loop.maintain_trailing(cl, SimpleNamespace(wallet="w"), None)
        assert moved == 0, "coin senza mid: skip, nessuno stop spostato"
        assert len(store.intents_open()) == 1, "l'intento resta aperto"
    finally:
        store.DB = old_db


if __name__ == "__main__":
    test_fmt_px_sigfig()
    test_fmt_px_decimals_cap()
    test_fmt_px_zero_collapse_raises()
    test_corr_count_on_returns()
    test_close_only_on_fill()
    test_reconcile_adopts_orphan()
    test_backup_if_due_creates_and_prunes()
    test_screener_skip_coin_fuori_registry()
    test_trailing_tollera_mid_mancante()
    print("OK: 9/9 regressioni audit passate")
