"""Dashboard istituzionale del paper-trading (T20): FastAPI + WS push.

Read-only sul bot: legge state/ (bot.db, cycle_report.json, bot.log), il mirror
HyPaper e il WS pubblico Hyperliquid. Processo separato, porta 8080, nessun
effetto sul loop. Push: tick 1s (prezzi/posizioni/equity), metrics 5s (KPI,
trades, market, agents, logs).

ponytail: metriche rolling (Sharpe/Sortino/win-rate mobile) calcolate lato
client dai trades; se la pagina diventa pesante, spostarle nell'aggregator.
"""
import asyncio
import contextlib
import json
import os
import signal
import time
from collections import deque

import requests
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from tradingagents.hyperliquid import store
from tradingagents.hyperliquid.config import load
from tradingagents.hyperliquid.data import HyPaperClient
from tradingagents.hyperliquid.loop import equity, load_dotenv

load_dotenv()
STATIC = os.path.join(os.path.dirname(__file__), "static")
TS_FMT = "%Y-%m-%dT%H:%M:%S%z"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
TAKER_FEE = 0.00035  # stima: entrambe le gambe market/IOC


def _parse_ts(s):
    try:
        return time.mktime(time.strptime(s, TS_FMT))
    except (ValueError, TypeError):
        return None


class Agg:
    """Aggregatore in-memory: unica fonte per tick e metrics."""

    def __init__(self):
        self.cfg = load()
        self.c = HyPaperClient(self.cfg.hypaper_url)
        self.watchlist = [x.strip().upper() for x in
                          os.getenv("HL_WATCHLIST", "BTC").split(",") if x.strip()]
        self._mkt, self._mkt_t = {}, 0.0    # dump screener.json del loop
        self.mids = {}
        self.hl_ws_ok = False
        self.last_rest_ok = 0.0          # ultimo successo mirror (HyPaper/HL REST)
        self.cycles = []                 # cycle_report.json parsato
        self.equity = deque(maxlen=30000)   # [(ts_s, equity)]
        # serie storica persistita (il bot non scrive equity nei cicli)
        _eqp = os.path.join(os.path.dirname(store.DB), "equity.jsonl")
        if os.path.exists(_eqp):
            with open(_eqp, encoding="utf-8") as _fh:
                for _ln in _fh:
                    try:
                        _t, _v = _ln.strip().split(",")
                        self.equity.append((float(_t), float(_v)))
                    except ValueError:
                        pass
        self.day_key = time.strftime("%Y-%m-%d", time.gmtime())
        self.day_start_eq = None
        self.last_kpis = {}
        self.ctxs_cache = ([], 0.0)      # (universe, ts)
        self._pos_ch = None              # ponytail: cache clearinghouseState
        self._pos_t = 0.0                #   (HyPaper /info ha rate limit; senza,
        self._eq_v = None                #   fast_loop+slow_loop fanno ~80 req/min
        self._eq_t = 0.0                 #   e prendono 429 a raffica)
        self.clients = set()

    # ---------- sorgenti ----------

    async def hl_ws_loop(self):
        import json as _json
        while True:
            try:
                async with websockets.connect(HL_WS_URL, ping_interval=20) as ws:
                    await ws.send(_json.dumps(
                        {"method": "subscribe", "subscription": {"type": "allMids"}}))
                    self.hl_ws_ok = True
                    async for raw in ws:
                        msg = _json.loads(raw)
                        if msg.get("channel") == "allMids":
                            self.mids = {k: float(v) for k, v
                                         in msg["data"]["mids"].items()}
            except Exception:
                self.hl_ws_ok = False
                await asyncio.sleep(5)

    def rest(self, payload):
        out = self.c._post("/info", payload)
        self.last_rest_ok = time.time()
        return out

    def load_cycles(self):
        path = os.path.join(os.path.dirname(store.DB), "cycle_report.json")
        recs = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        with contextlib.suppress(json.JSONDecodeError):
                            recs.append(json.loads(line))
        self.cycles = recs

    def backfill_equity(self):
        pts = sorted((_parse_ts(r["ts"]), float(r["equity"]))
                     for r in self.cycles if r.get("equity") and _parse_ts(r["ts"]))
        have = {t for t, _ in self.equity}
        for t, v in pts:
            if t not in have:
                self.equity.append((t, v))

    def positions_live(self):
        # ponytail: sotto rate-limit serve l'ultimo snapshot buono (max 5 min
        # stantio); upgrade = cache persistente su disco.
        try:
            if time.time() - self._pos_t > 30 or self._pos_ch is None:
                self._pos_ch = self.rest(
                    {"type": "clearinghouseState", "user": self.cfg.wallet})
                self._pos_t = time.time()
        except Exception as e:
            if self._pos_ch is None or time.time() - self._pos_t > 300:
                raise
            print(f"[positions] rate-limit, uso cache stantia: {e}", flush=True)
        ch = self._pos_ch
        now = time.time()
        out = []
        intents_open = {i["coin"]: dict(i) for i in store.intents_open()}
        for p in ch["assetPositions"]:
            pos = p["position"]
            sz = float(pos["szi"])
            if sz == 0:
                continue
            coin = pos["coin"]
            it = intents_open.get(coin, {})
            entry = float(pos["entryPx"] or 0)
            mark = float(pos.get("markPx") or self.mids.get(coin.split("-")[0], 0))
            upnl = float(pos.get("unrealizedPnl") or 0)
            out.append({
                "coin": coin, "side": "long" if sz > 0 else "short",
                "szi": abs(sz), "entry": entry, "mark": mark,
                "lev": float(pos.get("leverage", {}).get("value", 1)),
                "uPnL": round(upnl, 2),
                "uPnLPct": round(upnl / (entry * abs(sz)) * 100, 2) if entry else 0,
                "stop": it.get("stop_px"),
                "durS": int(now - _parse_ts(it["ts"])) if it.get("ts") else None,
            })
        out.sort(key=lambda x: x["uPnLPct"], reverse=True)
        return out


    def exit_px(self, coin, ts_s):
        """Prezzo di chiusura stimato: candela 1m più vicina a ts (niente userFills)."""
        try:
            cs = self.rest({"type": "candleSnapshot",
                            "req": {"coin": coin, "interval": "1m",
                                    "startTime": int((ts_s - 120) * 1000),
                                    "endTime": int((ts_s + 120) * 1000)}}) or []
            best = min(cs, key=lambda c: abs(int(c["t"]) / 1000 - ts_s))
            return float(best["c"])
        except Exception:
            return None

    def universe(self):
        uni, ts0 = self.ctxs_cache
        if time.time() - ts0 > 30 or not uni:
            meta = self.rest({"type": "metaAndAssetCtxs"})
            uni = [{"name": u["name"], **ctx} for u, ctx in zip(meta[0]["universe"],
                                                                meta[1])]
            uni.sort(key=lambda x: float(x.get("dayNtlVlm") or 0), reverse=True)
            self.ctxs_cache = (uni, time.time())
        return uni

    def equity_cached(self, ttl=30):
        if self._eq_v is None or time.time() - self._eq_t > ttl:
            self._eq_v = equity(self.c, self.cfg)
            self._eq_t = time.time()
        return self._eq_v

    def kpis(self, trades):
        eq_now = self.equity_cached()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if self.day_key != day or self.day_start_eq is None:
            self.day_key = day
            self.day_start_eq = eq_now

        closed = [t for t in trades if t["pnl"] is not None]
        pnls = [t["pnl"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gp, gl = sum(wins), -sum(losses)

        realized_today = sum(t["pnl"] for t in closed
                             if time.strftime("%Y-%m-%d", time.gmtime(
                                 _parse_ts(t["tsClose"]) or 0)) == day)
        pl = self.positions_live()
        unreal = sum(p["uPnL"] for p in pl)

        series = list(self.equity) + [(time.time(), eq_now)]
        peak, max_dd, dd_at_end = series[0][1], 0.0, 0.0
        for _, v in series:
            peak = max(peak, v)
            max_dd = max(max_dd, (peak - v) / peak)
        dd_at_end = (peak - series[-1][1]) / peak if peak else 0

        consec = record = 0
        for p in reversed(pnls):
            if p < 0:
                consec += 1
            else:
                break
        run = 0
        for p in pnls:
            run = run + 1 if p < 0 else 0
            record = max(record, run)

        scans = [r for r in self.cycles if "stage" not in r]
        proposals = [r for r in scans if r.get("llm_side")
                     and r.get("llm_side") != "flat" and "contro segnale" not in (r.get("reason") or "")]
        vetoes = [r for r in scans if str(r.get("reason", "")).startswith("veto")]
        executed = [r for r in scans if r["executed"]]
        errs24 = [r for r in self.cycles if r.get("stage") == "error"
                  and (_parse_ts(r.get("ts")) or 0) > time.time() - 86400]
        llm_calls = [r for r in scans if r.get("llm_ms")]
        hb_path = os.path.join(os.path.dirname(store.DB), "heartbeat")
        hb_age = time.time() - os.path.getmtime(hb_path) if os.path.exists(hb_path) else None

        tot_pnl = sum(pnls)
        return {
            "equity": eq_now, "seed": self.cfg.paper_seed_balance,
            "dayStart": self.day_start_eq,
            "dayPnl": eq_now - self.day_start_eq,
            "dayPnlPct": (eq_now / self.day_start_eq - 1) * 100 if self.day_start_eq else 0,
            "unrealized": unreal,
            "marginUsed": sum(p["szi"] * p["mark"] / max(1, p["lev"]) for p in pl),
            "levTot": sum(p["szi"] * p["mark"] for p in pl) / eq_now if eq_now else 0,
            "winRate": len(wins) / len(closed) * 100 if closed else None,
            "wins": len(wins), "losses": len(losses), "closes": len(closed),
            "profitFactor": gp / gl if gl > 0 else (None if gp == 0 else 99.0),
            "maxDD": max_dd * 100, "ddNow": dd_at_end * 100,
            "consecLosses": consec, "consecRecord": record,
            "avgWin": gp / len(wins) if wins else None,
            "avgLoss": gl / len(losses) if losses else None,
            "calmar": (tot_pnl / self.cfg.paper_seed_balance) / (max_dd or 1),
            "riskReward": (gp / len(wins)) / (gl / len(losses)) if wins and losses else None,
            "vetoRate": len(vetoes) / len(proposals) * 100 if proposals else 0,
            "cyclesTotal": len(self.cycles), "scans": len(scans),
            "executedN": len(executed),
            "errors24": len(errs24), "lastError": errs24[-1].get("error") if errs24 else None,
            "uptimeS": int(time.time() - (min((_parse_ts(r["ts"]) for r in self.cycles
                                               if _parse_ts(r["ts"])), default=time.time()))),
            "heartbeatAgeS": hb_age,
            "cycleNum": len(scans),
        }

    def market_file(self, ttl=1800):
        """Dump screener del loop (state/screener.json), ricaricato ogni 30 min."""
        if time.time() - self._mkt_t > ttl:
            p = os.path.join(os.path.dirname(store.DB), "screener.json")
            try:
                with open(p) as fh:
                    self._mkt = json.load(fh)
            except Exception:
                self._mkt = {}
            self._mkt_t = time.time()
        return self._mkt

    def watched(self):
        rows = self.market_file().get("rows")
        return [r["coin"] for r in rows] if rows else self.watchlist

    def market_rows(self):
        pos_coins = {p["coin"].split("-")[0] for p in self.positions_live()}
        last_z = {}
        for r in self.cycles:
            if "coin" in r and r.get("ofi_z") is not None:
                last_z[r["coin"]] = r["ofi_z"]
        trig = {r["coin"] for r in self.cycles
                if r.get("llm_side") and (_parse_ts(r["ts"]) or 0) > time.time() - 3600}
        mf = self.market_file()
        rows_src = mf.get("rows")
        if rows_src:
            trig = set(mf.get("triggered") or ())
            out = []
            for r in rows_src:
                fund = float(r["funding"] or 0)
                out.append({
                    "coin": r["coin"], "mark": r["mid"],
                    "chg24h": r.get("chg24h"), "vol24h": r["vol24h"],
                    "oi": r["oi"], "funding": fund * 24 * 100,
                    "fundingAnn": fund * 24 * 365 * 100,
                    "spread": r.get("spread_bps"), "ofiZ": r.get("ofi_z"),
                    "rsi": r.get("rsi"), "macdH": r.get("macd_h"),
                    "volX": r.get("vol_x"), "triggered": r["coin"] in trig,
                    "inPos": r["coin"] in pos_coins, "sigma": 1.0,
                })
            return out
        rows = []
        sigma = 1.0  # soglia visuale: z e' gia' standardizzato dal modulo signal
        for u in self.universe()[:40]:
            name = u["name"]
            mark = float(u.get("markPx") or self.mids.get(name, 0))
            prev = float(u.get("prevDayPx") or 0)
            fund = float(u.get("funding") or 0)
            spread = ((float(u["askPx"]) - float(u["bidPx"])) / mark * 100
                      if mark and u.get("askPx") and u.get("bidPx") else None)
            rows.append({
                "coin": name, "mark": mark,
                "chg24h": (mark / prev - 1) * 100 if prev else None,
                "vol24h": float(u.get("dayNtlVlm") or 0),
                "oi": float(u.get("openInterest") or 0) * mark,
                "funding": fund * 24 * 100, "fundingAnn": fund * 24 * 365 * 100,
                "spread": spread, "ofiZ": last_z.get(name),
                "triggered": name in trig, "inPos": name in pos_coins,
                "sigma": sigma,
            })
        return rows

    def agents_stats(self):
        scans = [r for r in self.cycles if "stage" not in r]
        today = [r for r in scans if (_parse_ts(r.get("ts")) or 0) > time.time() - 86400]
        llm_today = [r for r in today if r.get("llm_ms")]
        triggered = [r["coin"] for r in today if r.get("llm_side")]
        last_scan = scans[-1] if scans else None
        avg_dur = (sum(r.get("dur_s") or 0 for r in today) / len(today)) if today else None
        return {
            "watchlist": self.watched(),
            "scansToday": len(today),
            "triggered": triggered,
            "ratioText": f"{len(triggered)} trigger su {len(today)} scan",
            "llmCallsToday": len(llm_today),
            "llmMsAvg": round(sum(r["llm_ms"] for r in llm_today) / len(llm_today)) if llm_today else None,
            "avgDurS": round(avg_dur, 1) if avg_dur else None,
            "nodes": [
                {"name": "Screener", "lastTs": last_scan.get("ts") if last_scan else None},
                {"name": "Signal OFI", "lastTs": last_scan.get("ts") if last_scan else None},
                {"name": "Analysts", "lastTs": next((r["ts"] for r in reversed(scans) if r.get("panel")), None)},
                {"name": "Trader PM", "lastTs": next((r["ts"] for r in reversed(scans) if r.get("llm_side")), None)},
                {"name": "RiskManager", "lastTs": next((r["ts"] for r in reversed(scans) if r.get("reason", "").startswith("veto") or r["executed"]), None)},
                {"name": "Executor", "lastTs": next((r["ts"] for r in reversed(scans) if r["executed"]), None)},
            ],
            "recent": scans[-40:],
        }

    def build_trades(self):
        with store.connect() as conn:
            intents = [dict(r) for r in conn.execute(
                "SELECT * FROM intents ORDER BY id DESC").fetchall()]
        cyc_by_coin = {}
        for r in self.cycles:
            if r.get("coin"):
                cyc_by_coin.setdefault(r["coin"], []).append(r)
        out = []
        for it in intents:
            t0i = _parse_ts(it["ts"])
            rec = next((r for r in reversed(cyc_by_coin.get(it["coin"], []))
                        if r.get("llm_side") and (_parse_ts(r["ts"]) or 0) <= t0i), {})
            qty, entry = float(it["qty"]), float(it["entry_px"])
            sgn = 1 if str(it["side"]).lower().startswith(("long", "buy")) else -1
            tcs = _parse_ts(it["closed_ts"]) if it["closed_ts"] else None
            xp = self.exit_px(it["coin"], tcs) if tcs else None
            pnl = round((xp - entry) * qty * sgn, 2) if xp else None
            # ponytail: fee stimata taker (0.035%) su entrambe le gambe; HyPaper non
            # espone i fill reali (userFills rotto). Upgrade: sink Postgres HyPaper.
            fee = round((entry + (xp or entry)) * qty * TAKER_FEE, 4) if xp else None
            out.append({
                "id": it["id"], "coin": it["coin"], "side": it["side"],
                "qty": qty, "notional": entry * qty,
                "entry": entry, "exit": xp, "stop": it["stop_px"],
                "pnl": pnl, "fee": fee,
                "lev": None, "confidence": rec.get("confidence"),
                "rationale": rec.get("rationale"), "panel": rec.get("panel"),
                "debate": rec.get("debate"),
                "tsOpen": it["ts"], "tsClose": it["closed_ts"],
                "durS": int(tcs - t0i) if tcs else None,
                "closeReason": it["close_reason"] or ("stop" if xp else None),
                "status": "open" if it["status"] == "open" else "closed",
                "pnlPct": (pnl / (entry * qty) * 100) if pnl is not None else None,
            })
        return out

    def events(self, n=20):
        """Feed attività: ordini, chiusure/stop, trade eseguiti, veto, errori."""
        ev = []
        for it in store.intents_open():
            ev.append({"ts": it["ts"], "type": "order", "coin": it["coin"],
                       "text": f"open {it['side']} {it['qty']} @ {it['entry_px']}"})
        with store.connect() as conn:
            closed = conn.execute(
                "SELECT * FROM intents WHERE status!='open' ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for it in (dict(r) for r in closed):
            reason = it["close_reason"] or "chiuso"
            ev.append({"ts": it["closed_ts"], "type": "stop" if "stop" in reason else "close",
                       "coin": it["coin"], "text": reason})
        for r in self.cycles[-400:]:
            ts = r.get("ts")
            if r.get("stage") == "error":
                ev.append({"ts": ts, "type": "error", "coin": "",
                           "text": (r.get("error") or "")[:120]})
            elif r.get("executed"):
                ev.append({"ts": ts, "type": "trade", "coin": r.get("coin", ""),
                           "text": f"{r.get('llm_side', '')} conf {r.get('confidence', '—')}"})
            elif str(r.get("reason", "")).startswith("veto"):
                ev.append({"ts": ts, "type": "veto", "coin": r.get("coin", ""),
                           "text": r["reason"]})
            elif r.get("llm_side") and r.get("llm_side") != "flat":
                ev.append({"ts": ts, "type": "skip", "coin": r.get("coin", ""),
                           "text": r.get("reason") or "PM skip"})
        ev.sort(key=lambda e: _parse_ts(e["ts"]) or 0, reverse=True)
        return ev[:n]

    def logs_tail(self, n=200):
        path = os.path.join(os.path.dirname(store.DB), "bot.log")
        if not os.path.exists(path):
            return []
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
        return lines[-n:]

    def snapshot_slow(self):
        self.load_cycles()
        self.backfill_equity()
        trades = self.build_trades()
        k = self.kpis(trades)
        self.last_kpis = k
        return {
            "t": "metrics", "ts": int(time.time() * 1000),
            "kpis": k,
            "equity": [[int(t * 1000), v] for t, v in self.equity][-5000:],
            "trades": trades,
            "market": self.market_rows(),
            "agents": self.agents_stats(),
            "events": self.events(),
            "logs": self.logs_tail(),
            "cfg": {
                "mode": self.cfg.trading_mode, "wallet": self.cfg.wallet,
                "watchlist": self.watched(), "model": self.cfg.model,
                "base_frac": self.cfg.base_frac, "lev_cap": self.cfg.lev_cap,
                "daily_dd": self.cfg.daily_dd, "weekly_dd": self.cfg.weekly_dd,
                "atr_stop_mult": self.cfg.atr_stop_mult, "signal_z_min": self.cfg.signal_z_min,
                "scanIntervalS": int(os.getenv("HL_SCAN_INTERVAL", "900")),
                "wsCollectS": self.cfg.ws_collect_seconds,
            },
            "conn": {
                "hlws": self.hl_ws_ok,
                "hypaper": time.time() - self.last_rest_ok < 60,
                "hlrest": time.time() - self.last_rest_ok < 60,
                "router": any(r.get("llm_ms") for r in self.cycles[-50:]),
            },
        }


agg = Agg()
app = FastAPI(title="HL Paper Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
API_KEY = os.getenv("DASHBOARD_API_KEY", "")


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/api") \
            and request.headers.get("X-API-Key") != API_KEY:
        return PlainTextResponse("unauthorized", status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/snapshot")
async def api_snapshot():
    agg.load_cycles()
    agg.backfill_equity()
    trades = agg.build_trades()
    agg.last_kpis = agg.kpis(trades)
    return {"kpis": agg.last_kpis, "positions": agg.positions_live(),
            "mids": agg.mids}


@app.get("/api/trades")
async def api_trades():
    agg.load_cycles()
    return agg.build_trades()


@app.get("/api/report")
async def api_report():
    from tradingagents.hyperliquid.monitor import build_report
    return Response(build_report(), media_type="text/markdown")


@app.get("/api/candles/{coin}")
async def api_candles(coin: str, interval: str = "1h", hours: int = 48):
    try:
        return agg.c.candles(coin, interval, hours * 3600 * 1000)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/l2book/{coin}")
async def api_l2book(coin: str):
    try:
        return agg.rest({"type": "l2Book", "coin": coin})
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/funding/{coin}")
async def api_funding(coin: str):
    try:
        return agg.rest({"type": "fundingHistory", "coin": coin,
                         "startTime": int((time.time() - 7 * 86400) * 1000),
                         "endTime": int(time.time() * 1000)})
    except Exception as e:
        raise HTTPException(502, str(e))



async def broadcast(payload):
    dead = []
    data = json.dumps(payload, default=str)
    for ws in list(agg.clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        agg.clients.discard(ws)


async def fast_loop():
    while True:
        try:
            positions, eq_now = await asyncio.to_thread(
                lambda: (agg.positions_live(), agg.equity_cached()))
            k = agg.last_kpis
            ds = k.get("dayStart")
            await broadcast({"t": "tick", "ts": int(time.time() * 1000),
                             "mids": agg.mids, "positions": positions,
                             "equity": eq_now,
                             "dayPnl": eq_now - ds if ds else 0,
                             "dayPnlPct": (eq_now / ds - 1) * 100 if ds else 0,
                             "unrealized": sum(p["uPnL"] for p in positions)})
        except Exception as e:
            print(f"[fast_loop] {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(1)


async def slow_loop():
    while True:
        try:
            await broadcast(await asyncio.to_thread(agg.snapshot_slow))
            now = time.time()
            v = (agg.last_kpis or {}).get("equity")
            prev_t = agg.equity[-1][0] if agg.equity else 0
            if v and now - prev_t >= 60:
                agg.equity.append((now, float(v)))
                with open(os.path.join(os.path.dirname(store.DB), "equity.jsonl"),
                          "a", encoding="utf-8") as fh:
                    fh.write(f"{now},{v}\n")
        except Exception:
            import traceback
            print("[slow_loop] " + traceback.format_exc(), flush=True)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if API_KEY and ws.query_params.get("key") != API_KEY:
        await ws.close(code=4401)
        return
    await ws.accept()
    agg.clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive ping dal client
    except WebSocketDisconnect:
        pass
    finally:
        agg.clients.discard(ws)


@app.websocket("/{rest:path}")
async def ws_fallback(ws: WebSocket):
    # ponytail: i path-varianti (/ws/, /api/ws) altrimenti esplodono in
    # AssertionError dentro StaticFiles; chiusura pulita invece del 500.
    await ws.accept()
    await ws.close(code=4404)


@app.on_event("startup")
async def startup():
    # ponytail: load_cycles/backfill_equity qui possono bloccare il bind di
    # uvicorn su un wedge FUSE; snapshot_slow li richiama comunque ogni 5s.
    print("[startup] begin", flush=True)
    asyncio.create_task(agg.hl_ws_loop())
    asyncio.create_task(fast_loop())
    asyncio.create_task(slow_loop())
    print("[startup] tasks created", flush=True)


import faulthandler
faulthandler.register(signal.SIGUSR1)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
