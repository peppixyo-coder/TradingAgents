"""Self-check multi-asset: matematica pura di screener/scanner/risk, zero rete.

Run dentro il container: python /app/scripts/selfcheck_multiasset.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingagents.hyperliquid import risk, scanner  # noqa: E402
from tradingagents.hyperliquid.screener import is_stable  # noqa: E402

# kv in-memory: non toccare il DB reale del bot
KV = {}
import tradingagents.hyperliquid.store as store_mod  # noqa: E402
store_mod.kv_get = lambda k, d=None: KV.get(k, d)
store_mod.kv_set = lambda k, v: KV.__setitem__(k, str(v))


def approx(a, b, tol=0.5):
    return abs(a - b) <= tol


# ---- is_stable (euristico peg ratificato) ----
assert is_stable("USDT", 1.0)
assert is_stable("USDe", 0.999)
assert is_stable("EURC", 1.08)            # nome EUR* => stabile
assert not is_stable("BTC", 60000.0)
assert not is_stable("PURR", 1.02)        # vicino a $1 ma fuori banda peg

# ---- RSI(14) semplice ----
up = [float(i) for i in range(1, 40)]
assert scanner.rsi14(up) == 100.0
down = [float(i) for i in range(40, 1, -1)]
assert approx(scanner.rsi14(down), 0.0, 0.01)
flat = [100.0] * 40
assert scanner.rsi14(flat) == 50.0        # nessun movimento => neutro
assert scanner.rsi14(up[:10]) is None     # serie corta

# ---- MACD hist ----
assert scanner.macd_hist([100.0] * 34) is None
rising = [100.0 * (1.01 ** i) for i in range(60)]
assert scanner.macd_hist(rising) > 0      # trend rialzista => istogramma positivo

# ---- anomalia volumi ----
d1 = [{"v": 1.0}] * 30
assert approx(scanner.vol_anomaly(2000.0, d1, 2.0), 1000.0, 1e-9)  # 2000$ / media 2$
assert scanner.vol_anomaly(100.0, [], 2.0) is None

# ---- flow z: fallback e baseline vera ----
assert scanner.flow_z("NEW", 0.5) > 0     # pseudo-z=3f finche' baseline corta
for _ in range(9):
    scanner.flow_push("TST", 0.0)
scanner.flow_push("TST", 1.0)             # seq: nove 0 e un 1 => sd noto
assert approx(scanner.flow_z("TST", 1.0), 0.9 / (0.316 ** 2 * 10 / 9) ** 0.5, 0.3)

# ---- pearson ----
lin = [float(i) for i in range(30)]
assert scanner.pearson(lin, [2 * x + 1 for x in lin]) > 0.999
assert abs(scanner.pearson(lin, [(x % 2) - 0.5 for x in lin])) < 0.2
assert scanner.pearson(lin[:10], lin[:10]) == 0.0   # sotto n_min => 0, mai NaN

# ---- portfolio_veto ----
cfg = type("C", (), {"lev_cap": 3.0})()
pos = lambda coin, val, szi: {"position": {"coin": coin, "positionValue": val,
                                           "szi": szi}}
eq = 10_000
assert risk.portfolio_veto(cfg, eq, "ETH", 500, [pos("BTC", 400, 0.01)], 0) == []
v = risk.portfolio_veto(cfg, eq, "BTC", 700, [pos("BTC", 400, 0.01)], 0)
assert any("ASSET_CAP" in x for x in v), v          # 400+700 > 10% di 10k
v = risk.portfolio_veto(cfg, eq, "SOL", 16000, [pos("ETH", 15000, 5)], 0)
assert any("LEV_TOT" in x for x in v), v            # 31k > 3x eq
v = risk.portfolio_veto(cfg, eq, "PEPE", 100, [], 3)
assert any("CORR_CLUSTER" in x for x in v), v       # 3 correlati + 1 > 3
assert risk.portfolio_veto(cfg, 0, "BTC", 100, [], 0) == ["EQUITY<=0"]

# ---- ctx_deltas ----
prev = {"ETH": {"funding": 0.0001, "oi": 100.0}}
rows = [{"coin": "ETH", "funding": 0.0002, "oi": 150.0},
        {"coin": "BTC", "funding": 0.0001, "oi": 50.0}]
scanner.ctx_deltas(rows, prev)
assert approx(rows[0]["funding_delta"], 0.0001, 1e-9)
assert approx(rows[0]["oi_delta"], 0.5, 1e-9)
assert rows[1]["funding_delta"] is None             # coin nuovo: niente delta

print("selfcheck multiasset OK")
