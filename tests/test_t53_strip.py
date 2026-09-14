"""T53: frame metrics leggero + detail on-demand REST.

1. _light() strip-only: toglie SOLO panel/debate dal frame 5s;
2. route /api/trade/{id} e /api/scan: record COMPLETI on-demand;
3. agents_stats: recent e lastCycle strippati nel frame.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import server  # noqa: E402


def test_light_strip_only():
    recs = [{"ts": "2026-09-12T10:00:00+0000", "coin": "DOGE", "panel": "x" * 9000,
             "debate": "y" * 9000, "rationale": "z" * 9000, "executed": False, "reason": "z<3"},
            {"panel": "p2", "debate": "d2", "rationale": "r2", "stage": "analysts"}]
    out = server._light(recs)
    assert all("panel" not in r and "debate" not in r and "rationale" not in r for r in out), "heavy campi leaked"
    assert out[0]["coin"] == "DOGE" and out[0]["executed"] is False
    assert recs[0]["panel"], "_light non deve mutare gli originali"
    assert server._HEAVY == ("panel", "debate", "rationale")


def test_routes_detail(tmp_path):
    server.store.DB = os.path.join(str(tmp_path), "t.db")
    server.store.init()
    with server.store.connect() as conn:
        conn.execute(
            "INSERT INTO intents(ts, coin, side, qty, entry_px, stop_px, status, original_size, remaining_size) "
            "VALUES('2026-09-12T10:00:00+0000','DOGE','long',10,100,95,'open',10,10)")
    with open(os.path.join(str(tmp_path), "cycle_report.json"), "w", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-09-12T10:00:00+0000", "coin": "DOGE", "llm_side": "long", "panel": "PANEL", '
                 '"debate": "DEBATE", "executed": true}\n'
                 '{"ts": "2026-09-12T10:00:00+0000", "coin": "DOGE", "stage": "analysts"}\n')
    agg = server.Agg.__new__(server.Agg)     # niente __init__: no DB/rete
    agg._eq_v = None
    agg.last_kpis = {}
    server.agg = agg
    from fastapi.testclient import TestClient
    hdrs = {"X-API-Key": server.API_KEY} if server.API_KEY else {}
    with TestClient(server.app) as cli:
        r = cli.get("/api/trades", headers=hdrs)
        assert r.status_code == 200
        tid = r.json()[0]["id"]
        assert all("panel" not in t and "debate" not in t for t in r.json())
        # detail completo on-demand
        r = cli.get(f"/api/trade/{tid}", headers=hdrs)
        assert r.status_code == 200 and r.json()["panel"] == "PANEL"
        assert r.json()["debate"] == "DEBATE"
        r = cli.get("/api/trade/999", headers=hdrs)
        assert r.status_code == 404
        # scan: record completo, salta stage
        r = cli.get("/api/scan", params={"ts": "2026-09-12T10:00:00+0000",
                                         "coin": "DOGE"}, headers=hdrs)
        assert r.status_code == 200 and r.json()["panel"] == "PANEL"
        r = cli.get("/api/scan", params={"ts": "2026-09-12T09:00:00+0000",
                                         "coin": "DOGE"}, headers=hdrs)
        assert r.status_code == 404


def test_frame_metrics_strippato(tmp_path):
    """Il contratto T53 del frame: trades/recent/lastCycle strippati.
    snapshot_slow intero richiede HyPaper; qui i tre pezzi che il frame
    spedisce davvero, con lo stesso codice che snapshot_slow usa."""
    server.store.DB = os.path.join(str(tmp_path), "t.db")
    server.store.init()
    with server.store.connect() as conn:
        conn.execute(
            "INSERT INTO intents(ts, coin, side, qty, entry_px, stop_px, status, original_size, remaining_size) "
            "VALUES('2026-09-12T10:00:00+0000','DOGE','long',10,100,28.86,'open',10,10)")
    a = server.Agg.__new__(server.Agg)
    a.cycles = [
        {"ts": "2026-09-12T10:00:00+0000", "coin": "DOGE", "llm_side": "long",
         "panel": "P" * 8000, "debate": "D" * 8000, "executed": True, "rationale": "r"},
        {"ts": "2026-09-12T10:05:00+0000", "coin": "DOGE", "stage": "analysts"},
    ]
    a._mkt_t = 0.0        # market_file: reload subito, ma file assente -> {}
    a.watchlist = ["DOGE"]
    trades = server._light(a.build_trades())
    ag = a.agents_stats()
    for t in trades:
        assert "panel" not in t and "debate" not in t, "trades heavy nel frame"
        assert "rationale" not in t, "rationale heavy nel trades frame"
    for r in ag["recent"]:
        assert "panel" not in r and "debate" not in r, "recent heavy nel frame"
        assert "rationale" not in r, "rationale heavy nel recent frame"
    assert ag["lastCycle"]["rationale"] == "r"  # card overview: 1 record completo
    assert "panel" not in ag["lastCycle"] and "debate" not in ag["lastCycle"]
