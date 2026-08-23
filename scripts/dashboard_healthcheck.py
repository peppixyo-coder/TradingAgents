"""Healthcheck automatico della dashboard (:8080).

Verifica che ogni dato servito sia presente e valido:
  A. REST  -> /health, /api/snapshot (kpis/positions/mids), /api/trades
  B. WS    -> ws://.../ws: almeno 1 frame "tick" e 1 "metrics" con tutte le sezioni
  C. HTML  -> GET /: 200 + id degli elementi KPI presenti + app.js servito

Uscita 0 = tutto ok, 1 = almeno un failure. Eseguire DOPO ogni modifica al
backend del bot (vedi README, regola regression-dashboard).
"""
import asyncio
import json
import os
import sys

import requests

BASE = os.getenv("DASHBOARD_URL", "http://localhost:8080").rstrip("/")
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
WS_URL = BASE.replace("http", "ws", 1) + "/ws" + (f"?key={API_KEY}" if API_KEY else "")
HDRS = {"X-API-Key": API_KEY} if API_KEY else {}

# Campi KPI che la UI consuma (app.js renderKpiStatics e tab System).
KPI_FIELDS = ["equity", "seed", "dayStart", "dayPnl", "unrealized", "marginUsed",
              "levTot", "winRate", "profitFactor", "maxDD", "ddNow",
              "cyclesTotal", "executedN", "errors24", "uptimeS", "heartbeatAgeS"]
# Sezioni del frame metrics che alimentano le 6 tab.
METRICS_SECTIONS = ["kpis", "equity", "trades", "market", "agents",
                    "events", "logs", "cfg", "conn"]
POSITION_FIELDS = ["coin", "side", "szi", "entry", "mark", "lev", "uPnL"]
TRADE_FIELDS = ["coin", "side", "qty", "notional", "entry", "status"]
HTML_IDS = ["h-mode", "h-wallet", "k-lev", "k-margin", "dd-fill", "eq-chart",
            "tbl-pos"]
ASSETS = ["/app.js", "/style.css",
          "/vendor/apexcharts.min.js",
          "/vendor/lightweight-charts.standalone.production.js"]


def bad(val):
    return val is None or val == "" or str(val) == "NaN"


def check_rest(checks):
    try:
        r = requests.get(f"{BASE}/health", headers=HDRS, timeout=10)
        checks.append(("rest:/health 200", r.status_code == 200, r.status_code))
    except Exception as e:
        checks.append(("rest:/health raggiungibile", False, repr(e)))
        return
    try:
        s = requests.get(f"{BASE}/api/snapshot", headers=HDRS, timeout=30).json()
    except Exception as e:
        checks.append(("rest:/api/snapshot valido", False, repr(e)))
        return
    kpis = s.get("kpis") or {}
    for f in KPI_FIELDS:
        v = kpis.get(f)
        checks.append((f"kpi:{f}", not bad(v), v))
    positions = s.get("positions") or []
    checks.append(("positions:n", isinstance(positions, list), len(positions)))
    for p in positions:
        for f in POSITION_FIELDS:
            v = p.get(f)
            checks.append((f"pos:{p.get('coin','?')}:{f}", not bad(v), v))
    mids = s.get("mids") or {}
    checks.append(("mids:n>5", len(mids) > 5, len(mids)))
    try:
        trades = requests.get(f"{BASE}/api/trades", headers=HDRS, timeout=30).json()
    except Exception as e:
        checks.append(("rest:/api/trades valido", False, repr(e)))
        return
    checks.append(("trades:list", isinstance(trades, list),
                   len(trades) if isinstance(trades, list) else type(trades).__name__))
    for t in (trades or [])[:3]:
        for f in TRADE_FIELDS:
            v = t.get(f)
            checks.append((f"trade#{t.get('id','?')}:{f}", not bad(v), v))


async def _collect_ws(frames, want_s):
    import websockets
    async with websockets.connect(WS_URL, open_timeout=10) as ws:
        while sum(1 for f in frames if f.get("t") == "metrics") < 1 \
                and len(frames) < 50:
            raw = await asyncio.wait_for(ws.recv(), timeout=want_s)
            with_context = json.loads(raw)
            frames.append(with_context)


def check_ws(checks):
    frames = []
    try:
        asyncio.run(_collect_ws(frames, want_s=15))
    except Exception as e:
        checks.append(("ws:connesso+frame", False, repr(e)))
        return
    ticks = [f for f in frames if f.get("t") == "tick"]
    metrics = [f for f in frames if f.get("t") == "metrics"]
    checks.append(("ws:tick_frame_1s", bool(ticks), len(ticks)))
    checks.append(("ws:metrics_frame_5s", bool(metrics), len(metrics)))
    if not metrics:
        return
    m = metrics[0]
    for sec in METRICS_SECTIONS:
        v = m.get(sec)
        ok = v is not None
        if sec in ("market", "agents"):
            ok = ok and bool(v)
        checks.append((f"ws.metrics:{sec}", ok,
                       "<presente>" if ok else "MANCANTE"))


def check_html(checks):
    try:
        r = requests.get(BASE + "/", headers=HDRS, timeout=10)
    except Exception as e:
        checks.append(("html:GET /", False, repr(e)))
        return
    checks.append(("html:status_200", r.status_code == 200, r.status_code))
    html = r.text or ""
    for i in HTML_IDS:
        checks.append((f'html:id="{i}"', f'id="{i}"' in html, "ok" if f'id="{i}"' in html else "ASSENTE"))
    js_ref = "app.js" in html
    checks.append(("html:app.js_referenziato", js_ref, js_ref))
    for a in ASSETS:
        try:
            ra = requests.get(BASE + a, headers=HDRS, timeout=10)
            ok = ra.status_code == 200 and len(ra.content) > 500
            checks.append((f"html:servito {a}", ok,
                           f"{ra.status_code} {len(ra.content)}B"))
        except Exception as e:
            checks.append((f"html:servito {a}", False, repr(e)))


def main():
    checks = []
    check_rest(checks)
    check_ws(checks)
    check_html(checks)

    failed = [(n, v) for n, ok, v in checks if not ok]
    passed = [(n, v) for n, ok, v in checks if ok]
    print(f"\n{'=' * 60}\nDASHBOARD HEALTHCHECK ({BASE})\n{'=' * 60}")
    print(f"PASSED: {len(passed)}/{len(checks)}")
    print(f"FAILED: {len(failed)}/{len(checks)}")
    if failed:
        print(f"\n{'-' * 60}\nFAILURES:")
        for name, val in failed:
            print(f"  x {name} = {val}")
    print(f"\n{'-' * 60}\nALL VALUES:")
    for name, ok, val in checks:
        print(f"  {'+' if ok else 'x'} {name} = {val}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
