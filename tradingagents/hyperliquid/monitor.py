"""Report sintetico manuale del paper-trading (T17).

Run:  python -m tradingagents.hyperliquid.monitor
Legge state/cycle_report.json (cicli), state/bot.db (intents) e il mirror
HyPaper (userFills + account). Stampa a schermo e salva reports/report_<ts>.md.
"""
import json
import os
import time

from . import store
from .config import load
from .data import HyPaperClient
from .loop import equity, load_dotenv

load_dotenv()
STATE = os.path.dirname(store.DB)
REPORTS = os.path.normpath(os.path.join(STATE, "..", "reports"))
TS_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _load_cycles():
    path = os.path.join(STATE, "cycle_report.json")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _parse_ts(s):
    try:
        return time.mktime(time.strptime(s, TS_FMT))
    except (ValueError, TypeError):
        return None


def build_report():
    cfg = load()
    c = HyPaperClient(cfg.hypaper_url)
    recs = _load_cycles()

    scans = [r for r in recs if "stage" not in r]          # scansioni vere (done())
    opens = sum(1 for r in recs if r.get("stage") == "open")
    errs = [r for r in recs if r.get("stage") == "error"]
    day_ago = time.time() - 86400
    errs24 = [r for r in errs if (_parse_ts(r.get("ts")) or 0) > day_ago]
    llm_called = [r for r in scans if r.get("llm_side")]
    holds = [r for r in scans if not r["executed"]]
    trades = [r for r in scans if r["executed"]]

    with store.connect() as conn:
        intents = conn.execute("SELECT * FROM intents").fetchall()
    by_side = {"long": 0, "short": 0}
    by_coin = {}
    for it in intents:
        by_side[it["side"]] = by_side.get(it["side"], 0) + 1
        by_coin[it["coin"]] = by_coin.get(it["coin"], 0) + 1
    top_coin = max(by_coin, key=by_coin.get) if by_coin else "n/d"

    fills = c._post("/info", {"type": "userFills", "user": cfg.wallet})
    closes = [f for f in fills if str(f.get("dir", "")).startswith("Close")]
    pnl_list = [float(f["closedPnl"]) for f in closes]
    realized = sum(pnl_list)
    fees = sum(float(f.get("fee") or 0) for f in fills)
    wins = sum(1 for p in pnl_list if p > 0)
    winrate = wins / len(closes) * 100 if closes else None

    ch = c.clearinghouse_state(cfg.wallet)
    unreal = sum(float(p["position"].get("unrealizedPnl") or 0)
                 for p in ch["assetPositions"] if float(p["position"]["szi"]) != 0)
    eq_now = equity(c, cfg)

    series = [float(r["equity"]) for r in recs if r.get("equity")] + [eq_now]
    peak, max_dd = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak)

    first_ts = next((r["ts"] for r in recs if _parse_ts(r["ts"])), None)
    if first_ts:
        up_s = time.time() - _parse_ts(first_ts)
        uptime = f"{int(up_s // 86400)}g {int(up_s % 86400 // 3600)}h {int(up_s % 3600 // 60)}m"
    else:
        uptime = "n/d"

    pnl_tot = realized + unreal
    L = []
    L.append(f"# Report paper-trading — {time.strftime('%Y-%m-%d %H:%M')}")
    L.append(f"Wallet `{cfg.wallet}` · watchlist BTC · seed ${cfg.paper_seed_balance:,.0f}")
    L.append("")
    L.append(f"- **Cicli registrati**: {len(recs)} "
             f"({len(scans)} scansioni, {opens} skip posizione aperta, {len(errs)} errori)")
    L.append(f"- **Scan → trigger LLM**: {len(llm_called)}/{len(scans)} "
             f"({(len(llm_called) / len(scans) * 100 if scans else 0):.0f}%)")
    L.append(f"- **Trade eseguiti**: {len(trades)} "
             f"(long {by_side['long']} / short {by_side['short']} / hold {len(holds)})")
    L.append(f"- **PnL realizzato**: {realized:+,.2f}$ (fee {fees:,.2f}$) · "
             f"**irrealizzato**: {unreal:+,.2f}$ · **totale**: {pnl_tot:+,.2f}$ "
             f"({pnl_tot / cfg.paper_seed_balance * 100:+.2f}% su seed)")
    L.append(f"- **Win rate**: {winrate:.0f}% ({wins}/{len(closes)} chiusure)"
             if winrate is not None else "- **Win rate**: n/d (nessuna chiusura)")
    L.append(f"- **Max drawdown**: {max_dd * 100:.2f}% (serie equity: {len(series)} punti)")
    L.append(f"- **Asset più tradato**: {top_coin} ({by_coin.get(top_coin, 0)} intenti)")
    L.append(f"- **Errori ultime 24h**: {len(errs24)}"
             + (f" — ultimo: {errs24[-1].get('error', '')[:120]}" if errs24 else ""))
    L.append(f"- **Uptime**: {uptime} (dal primo ciclo registrato)")
    L.append("")
    L.append("## Posizioni aperte")
    live = [p["position"] for p in ch["assetPositions"] if float(p["position"]["szi"]) != 0]
    if not live:
        L.append("- nessuna")
    for p in live:
        L.append(f"- {p['coin']} szi={p['szi']} entry={p.get('entryPx')} "
                 f"uPnL={p.get('unrealizedPnl')}")
    L.append("")
    L.append("> ponytail: max DD su equity campionata ai soli trade + punto corrente;")
    L.append("> serie densa richiede campionamento per ciclo (upgrade al gate 15%).")
    return "\n".join(L)




def main(argv=None):
    report = build_report()
    print(report)
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS,
                        f"report_{time.strftime('%Y%m%d_%H%M')}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(f"\n[salvato] {path}")


if __name__ == "__main__":
    main()
