/* HL Paper Desk — client WS + rendering. Nessun framework. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { mids: {}, positions: [], k: {}, equity: [], trades: [], market: [],
            agents: {}, cfg: {}, conn: {}, logs: [], scans: [], tab: "overview",
            apCoin: null, charts: {} };

/* ---------- formatters ---------- */
const usd = v => v == null || isNaN(v) ? "—" :
  (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });
const px = v => v == null ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 });
const pct = (v, d = 2) => v == null || isNaN(v) ? "—" : (v > 0 ? "+" : "") + v.toFixed(d) + "%";
const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
const num = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d });
const dur = s => s == null ? "—" :
  s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}g`;
const ago = tsStr => { const t = typeof tsStr === "number" ? tsStr / 1000 : Date.parse(tsStr);
  return t ? dur((Date.now() - t) / 1000) + " fa" : "—"; };

/* ---------- websocket ---------- */
let ws, wsTimer;
function wsConnect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const key = new URLSearchParams(location.search).get("key");
  ws = new WebSocket(`${proto}://${location.host}/ws${key ? "?key=" + key : ""}`);
  ws.onopen = () => $("#reconnect").classList.remove("on");
  ws.onclose = () => { $("#reconnect").classList.add("on");
    wsTimer = setTimeout(wsConnect, 2000); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.t === "tick") onTick(m); else if (m.t === "metrics") onMetrics(m);
  };
  setInterval(() => ws.readyState === 1 && ws.send("ping"), 15000);
}

/* ---------- tick (1s) ---------- */
function onTick(m) {
  S.mids = m.mids; S.positions = m.positions;
  setTxt("k-equity", usd(m.equity));
  setDayPnl(m.dayPnl, m.dayPnlPct);
  setTxt("k-upnl", usd(m.unrealized));
  $("#k-upnl").className = "mono " + cls(m.unrealized);
  renderPositions();
  if (S.tab === "market") markLiveRows();
  const ap = S.charts.ap;
  if (ap && S.apCoin && S.mids[S.apCoin]) {
    $("#ap-mid").textContent = "mid " + px(S.mids[S.apCoin]);
  }
}
function setTxt(id, v) { const e = $("#" + id); if (e) e.textContent = v; }
function setDayPnl(v, p) {
  setTxt("k-daypnl", usd(v)); $("#k-daypnl").className = "mono " + cls(v);
  setTxt("k-daypctp", pct(p)); $("#k-daypctp").className = cls(v);
}

/* ---------- metrics (5s) ---------- */
function onMetrics(m) {
  S.k = m.kpis; S.equity = m.equity; S.trades = m.trades; S.market = m.market;
  S.agents = m.agents; S.logs = m.logs; S.cfg = m.cfg; S.conn = m.conn;
  S.scans = (m.agents.recent || []).slice().reverse();
  renderConn(m.conn); renderHeaderMeta(); renderKpiStatics();
  if (S.tab === "overview") renderOverview();
  if (S.tab === "trades") renderTrades();
  if (S.tab === "analytics") renderAnalytics();
  if (S.tab === "agents") renderAgents();
  if (S.tab === "market") renderMarket();
  if (S.tab === "system") renderSystem();
}

function renderConn(c) {
  dot("d-hlws", c.hlws); dot("d-hypaper", c.hypaper); dot("d-router", c.router);
  const hb = S.k.heartbeatAgeS;
  setTxt("h-heartbeat", hb == null ? "no heartbeat" : "hb " + dur(hb) + " fa");
  $("#h-heartbeat").style.color = hb != null && hb < 300 ? "" : "var(--neg)";
}
function dot(id, ok) { const e = $("#" + id); e.className = "dot " + (ok ? "on" : "off"); }
function renderHeaderMeta() {
  setTxt("h-mode", S.cfg.mode || "—"); setTxt("h-wallet", S.cfg.wallet || "—");
  setTxt("h-model", S.cfg.model || "—");
}
function renderKpiStatics() {
  const k = S.k;
  setTxt("k-seed", "seed " + usd(k.seed));
  setTxt("k-wr", k.winRate == null ? "—" : k.winRate.toFixed(1) + "%");
  setTxt("k-wr-n", `${k.wins || 0}W / ${k.losses || 0}L · PF ${num(k.profitFactor, 2)}`);
  setTxt("k-dd", k.maxDD == null ? "—" : "−" + k.maxDD.toFixed(1) + "%");
  setTxt("k-ddnow", "ora −" + (k.ddNow || 0).toFixed(1) + "%");
  setTxt("k-upnl-n", k.closes != null ? `${k.closes} chiusi · ${k.executedN || 0} eseguiti` : "");
}

/* ---------- overview ---------- */
function renderOverview() { drawEquity(); renderPositions(); renderLastCycle(); renderRisk(); }
function drawEquity() {
  if (!S.equity.length) return;
  let ch = S.charts.eq;
  if (!ch) {
    ch = LightweightCharts.createChart($("#eq-chart"), {
      layout: { background: { color: "transparent" }, textColor: "#8B94A3",
        fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#1F2630" }, horzLines: { color: "#1F2630" } },
      timeScale: { timeVisible: true, borderColor: "#1F2630" },
      rightPriceScale: { borderColor: "#1F2630" },
      crosshair: { mode: 0 }, autoSize: true });
    ch.addAreaSeries({ lineColor: "#4C8DFF", topColor: "rgba(76,141,255,.25)",
      bottomColor: "rgba(76,141,255,0)", lineWidth: 2, priceFormat: { type: "price", precision: 2 } });
    S.charts.eq = ch;
  }
  ch.series()[0].setData(S.equity.map(([t, v]) => ({ time: Math.floor(t / 1000), value: v })));
  ch.timeScale().fitContent();
}
function renderPositions() {
  const tb = $("#tbl-pos tbody"); const ps = S.positions;
  $("#pos-count").textContent = ps.length + " aperte";
  $("#pos-empty").style.display = ps.length ? "none" : "";
  tb.innerHTML = ps.map(p => `<tr>
    <td>${p.coin}</td><td class="${p.side}">${p.side}</td>
    <td>${num(p.szi, 4)}</td><td>${px(p.entry)}</td><td>${px(p.mark)}</td>
    <td>${p.lev}x</td><td class="${cls(p.uPnL)}">${usd(p.uPnL)}</td>
    <td class="${cls(p.uPnLPct)}">${pct(p.uPnLPct)}</td>
    <td>${p.stop ? px(p.stop) : "—"}</td><td>${dur(p.durS)}</td></tr>`).join("");
}
function renderLastCycle() {
  const r = S.agents.lastCycle; if (!r) { $("#last-cycle").innerHTML = "<p class='empty'>nessun ciclo registrato.</p>"; return; }
  $("#lc-ts").textContent = r.ts;
  $("#last-cycle").innerHTML = kv([
    ["Coin", r.coin], ["Esito", r.executed ? `<b class="pos">TRADE</b>` : `<b>${r.reason || "skip"}</b>`],
    ["OFI z", num(r.ofi_z, 2)], ["Conviction", r.conviction ?? "—"],
    ["LLM side", r.llm_side || "—"], ["LLM ms", r.llm_ms ? num(r.llm_ms, 0) : "—"],
    ["Durata ciclo", r.dur_s ? r.dur_s + "s" : "—"],
    ["Rationale", `<span class="sans" style="text-align:right">${esc(r.rationale || "—")}</span>`],
  ]);
}
function renderRisk() {
  const k = S.k, c = S.cfg;
  $("#risk-card").innerHTML = kv([
    ["DD corrente", `<b class="${k.ddNow > 0 ? "warn" : ""}">−${(k.ddNow || 0).toFixed(2)}%</b>`],
    ["Max DD storico", "−" + (k.maxDD || 0).toFixed(2) + "%"],
    ["Perdite consecutive", `${k.consecLosses || 0} (record ${k.consecRecord || 0})`],
    ["Veto rate", (k.vetoRate || 0).toFixed(0) + "%"],
    ["Lev cap", c.lev_cap + "x"], ["Frac base", (c.base_frac * 100) + "%"],
    ["Max posizioni", c.max_concurrent], ["DD giornaliero", (c.daily_dd * 100) + "%"],
  ]);
}
const kv = pairs => pairs.map(([l, v]) => `<label>${l}</label><b>${v}</b>`).join("");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- trades ---------- */
function filteredTrades() {
  const fs = $("#f-status").value, fo = $("#f-outcome").value, fc = $("#f-coin").value.toUpperCase();
  return S.trades.filter(t =>
    (!fs || t.status === fs) &&
    (!fo || (fo === "win" ? t.pnl > 0 : t.pnl < 0)) &&
    (!fc || t.coin.startsWith(fc)));
}
function renderTrades() {
  const rows = filteredTrades();
  const closed = rows.filter(t => t.status === "closed");
  const tot = closed.reduce((a, t) => a + t.pnl, 0);
  const wins = closed.filter(t => t.pnl > 0).length;
  $("#t-sum").innerHTML =
    `<span>${rows.length} trade</span><span>chiusi <b>${closed.length}</b></span>` +
    `<span>P&L <b class="${cls(tot)}">${usd(tot)}</b></span>` +
    `<span>win <b>${closed.length ? Math.round(wins / closed.length * 100) : 0}%</b></span>`;
  $("#tbl-trades tbody").innerHTML = rows.map(t => `
    <tr class="click" data-id="${t.id}">
      <td>${t.id}</td><td>${t.tsOpen}</td><td>${t.coin}</td>
      <td class="${t.side}">${t.side}</td><td>${usd(t.notional)}</td>
      <td>${px(t.entry)}</td><td>${t.exit != null ? px(t.exit) : "—"}</td>
      <td class="${cls(t.pnl)}">${t.pnl != null ? usd(t.pnl) : "—"}</td>
      <td class="${cls(t.pnlPct)}">${t.pnlPct != null ? pct(t.pnlPct) : "—"}</td>
      <td>${dur(t.durS)}</td><td class="sans">${esc(t.closeReason || t.status)}</td>
      <td class="hint">▾</td></tr>`).join("") ||
    `<tr><td colspan="12" class="empty">nessun trade.</td></tr>`;
}
function toggleTradeRow(tr) {
  const id = +tr.dataset.id;
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("xrow")) { next.remove(); tr.classList.remove("sel"); return; }
  $$("#tbl-trades tr.xrow").forEach(e => e.remove());
  $$("#tbl-trades tr.sel").forEach(e => e.classList.remove("sel"));
  const t = S.trades.find(x => x.id === id); if (!t) return;
  tr.classList.add("sel");
  const p = t.panel || {};
  const tr2 = document.createElement("tr"); tr2.className = "xrow";
  tr2.innerHTML = `<td colspan="12"><div class="detail-grid">
    <div><h3 class="card-h" style="margin-top:0">Decisione</h3><div class="kv cols">${kv([
      ["Confidence", t.confidence ?? "—"], ["Stop teorico", t.stop ? px(t.stop) : "—"],
      ["Fee", t.fee != null ? usd(t.fee) : "—"], ["Chiuso", t.tsClose || "aperto"]])}</div>
      <div class="box" style="margin-top:8px">${esc(t.rationale || "—")}</div></div>
    <div><h3 class="card-h" style="margin-top:0">Panel</h3>
      <div class="box">${esc(p.bull || "—")}</div>
      <div class="box" style="margin-top:6px">${esc(p.bear || "—")}</div></div>
    <div><h3 class="card-h" style="margin-top:0">Debate</h3><div class="box">${esc(t.debate || "—")}</div></div>
  </div></td>`;
  tr.after(tr2);
}

/* ---------- analytics ---------- */
function renderAnalytics() {
  const cl = S.trades.filter(t => t.status === "closed")
    .sort((a, b) => Date.parse(a.tsOpen) - Date.parse(b.tsOpen));
  drawDist(cl); drawRoll(cl); drawCum(cl); drawR(cl);
  renderAnStats(cl);
}
function drawDist(cl) {
  if (!cl.length) return emptyChart("an-dist");
  const buckets = [-50, -20, -10, -5, 0, 5, 10, 20, 50, Infinity];
  const labels = ["<-50", "-50/-20", "-20/-10", "-10/-5", "-5/0", "0/5", "5/10", "10/20", "20/50", ">50"];
  const counts = Array(10).fill(0);
  cl.forEach(t => { const v = t.pnlPct || 0;
    counts[buckets.findIndex((b, i) => v < b || i === 9)]++; });
  chart("an-dist", { series: [{ name: "trade", data: counts }], type: "bar",
    colors: counts.map(c => null), x: labels });
}
function drawRoll(cl) {
  if (cl.length < 3) return emptyChart("an-roll");
  const w = 10, data = [];
  for (let i = w - 1; i < cl.length; i++)
    data.push([cl[i].tsOpen, cl.slice(i - w + 1, i + 1).filter(t => t.pnl > 0).length / w * 100]);
  chart("an-roll", { series: [{ name: "wr%", data }], type: "line", colors: ["#4C8DFF"], min: 0, max: 100 });
}
function drawCum(cl) {
  if (!cl.length) return emptyChart("an-cum");
  let c = 0;
  const data = cl.map(t => (c += t.pnl, [t.tsClose || t.tsOpen, +c.toFixed(2)]));
  chart("an-cum", { series: [{ name: "PnL$", data }], type: "area", colors: ["#26A69A"] });
}
function drawR(cl) {
  const data = cl.map(t => {
    const risk = t.stop && t.entry ? Math.abs(t.entry - t.stop) * t.qty : t.notional * 0.01;
    return [t.tsOpen, risk ? +(t.pnl / risk).toFixed(2) : 0];
  });
  chart("an-r", { series: [{ name: "R", data }], type: "bar", colors: ["#E8B341"] });
}
function renderAnStats(cl) {
  const k = S.k;
  const wins = cl.filter(t => t.pnl > 0), losses = cl.filter(t => t.pnl < 0);
  $("#an-stats").innerHTML = "<h3>Resoconto</h3>" + kv([
    ["Trade chiusi", cl.length], ["Win/Loss", `${wins.length}/${losses.length}`],
    ["Profit factor", num(k.profitFactor, 2)], ["Avg win", usd(k.avgWin)],
    ["Avg loss", usd(k.avgLoss)], ["Risk:Reward", num(k.riskReward, 2)],
    ["Realizzato tot.", `<span class="${cls(k.realizedTot)}">${usd(k.realizedTot)}</span>`],
    ["Fee tot.", usd(k.feesTot)], ["Calmar (sempl.)", num(k.calmar, 2)]]);
  $("#an-streaks").innerHTML = "<h3>Streak</h3>" + kv([
    ["Perd. consecutive", k.consecLosses || 0], ["Record negativo", k.consecRecord || 0]]);
  $("#an-flow").innerHTML = "<h3>Flusso decisioni</h3>" + kv([
    ["Scan totali", k.scans || 0], ["Esecuzioni", k.executedN || 0],
    ["Veto rate", (k.vetoRate || 0).toFixed(0) + "%"],
    ["Trigger 1h", (S.market || []).filter(m => m.triggered).length],
    ["Cicli registrati", k.cyclesTotal || 0]]);
}
let apexInstances = {};
function chart(id, { series, type, colors, x, min, max }) {
  if (apexInstances[id]) apexInstances[id].destroy();
  const palette = ["#26A69A", "#EF5350", "#4C8DFF", "#E8B341"];
  const options = {
    chart: { type: type === "area" ? "area" : type, height: 260, background: "transparent",
      fontFamily: "IBM Plex Mono, monospace", toolbar: { show: false }, animations: { enabled: false },
      stacked: false },
    theme: { mode: "dark" },
    colors: colors || palette,
    stroke: { curve: "smooth", width: type === "bar" ? 0 : 2 },
    fill: type === "area" ? { type: "gradient", opacity: [.25, 0] } :
      type === "bar" ? { opacity: .8 } : {},
    dataLabels: { enabled: false },
    xaxis: { type: x ? "category" : "datetime", categories: x,
      labels: { style: { fontSize: "10px" } }, axisBorder: { show: false } },
    yaxis: { min, max, labels: { formatter: v => Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : (+v).toFixed(1) } },
    grid: { borderColor: "#1F2630" },
    tooltip: { theme: "dark" },
    legend: { show: false },
    series,
  };
  apexInstances[id] = new ApexCharts($("#" + id), options);
  apexInstances[id].render();
}
function emptyChart(id) { if (apexInstances[id]) { apexInstances[id].destroy(); delete apexInstances[id]; }
  $("#" + id).innerHTML = "<p class='empty'>dati insufficienti.</p>"; }

/* ---------- agents ---------- */
function renderAgents() {
  const a = S.agents;
  $("#ag-ratio").textContent = a.ratioText || "";
  const now = Date.now();
  $("#ag-pipe").innerHTML = (a.nodes || []).map(n => {
    const t = n.lastTs ? Date.parse(n.lastTs) : 0;
    const age = t ? (now - t) / 1000 : null;
    const st = age == null ? ["idle", "mai"] :
      age < 300 ? ["ok", ago(n.lastTs)] : ["stale", ago(n.lastTs)];
    return `<li><b>${n.name}</b><span class="hint">${n.lastTs || ""}</span>
      <span class="st ${st[0]}">${st[1]}</span></li>`;
  }).join("");
  $("#ag-stats").innerHTML = kv([
    ["Scan oggi", a.scansToday ?? 0], ["Trigger oggi", (a.triggered || []).join(", ") || "—"],
    ["Chiamate LLM", a.llmCallsToday ?? 0], ["Latenza LLM media", a.llmMsAvg ? a.llmMsAvg + " ms" : "—"],
    ["Durata ciclo media", a.avgDurS ? a.avgDurS + " s" : "—"],
    ["Watchlist", (a.watchlist || []).join(", ")],
    ["Intervalli", `scan ${S.cfg.scanIntervalS}s · ws ${S.cfg.wsCollectS}s`]]);
  const sel = $("#ag-cycle-sel");
  sel.innerHTML = S.scans.map((r, i) =>
    `<option value="${i}">${r.ts} · ${r.coin} · ${r.executed ? "TRADE" : (r.reason || "skip")}</option>`).join("");
  if (S.scans.length) showCycle(0);
}
function showCycle(i) {
  const r = S.scans[i]; if (!r) return;
  const p = r.panel || {};
  $("#ag-decision").innerHTML = kv([
    ["Coin", r.coin], ["Segnale quant", r.ofi_z != null ? "z=" + num(r.ofi_z, 2) : "—"],
    ["LLM side", r.llm_side || "—"], ["Conviction", r.confidence ?? "—"],
    ["Esito", r.executed ? '<b class="pos">TRADE</b>' : esc(r.reason || "—")],
    ["Latenza LLM", r.llm_ms ? r.llm_ms + " ms" : "—"],
    ["Durata", r.dur_s ? r.dur_s + " s" : "—"]]);
  $("#ag-decision").insertAdjacentHTML("beforeend",
    `<label>Rationale</label><b class="sans" style="white-space:normal">${esc(r.rationale || "—")}</b>`);
  $("#ag-bull").textContent = p.bull || "—";
  $("#ag-bear").textContent = p.bear || "—";
  $("#ag-debate").textContent = r.debate || "—";
}

/* ---------- market ---------- */
function renderMarket() {
  const f = $("#mkt-filter").value.toUpperCase();
  const rows = S.market.filter(m => !f || m.coin.includes(f));
  $("#tbl-market tbody").innerHTML = rows.map(m => `
    <tr class="click ${S.apCoin === m.coin ? "sel" : ""}" data-coin="${m.coin}">
      <td>${m.coin}${m.inPos ? ' <span class="chip mode">pos</span>' : ""}${m.triggered ? ' <span class="warn">●</span>' : ""}</td>
      <td data-live="${m.coin}">${px(m.mark)}</td>
      <td class="${cls(m.chg24h)}">${pct(m.chg24h)}</td>
      <td class="${cls(-m.fundingAnn)}">${pct(m.fundingAnn)}</td>
      <td>${usd(m.oi)}</td><td>${usd(m.vol24h)}</td>
      <td>${m.spread != null ? (m.spread * 100).toFixed(1) : "—"}</td>
      <td class="${m.ofiZ != null ? cls(m.ofiZ) : ""}">${m.ofiZ != null ? num(m.ofiZ, 2) : "—"}</td>
      <td class="hint">›</td></tr>`).join("");
}
function markLiveRows() {
  $$("#tbl-market td[data-live]").forEach(td => {
    const p = S.mids[td.dataset.live]; if (p) td.textContent = px(p);
  });
}
async function openAsset(coin) {
  S.apCoin = coin;
  $("#ap-title").textContent = coin + "-PERP";
  $$("#tbl-market tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.coin === coin));
  try {
    const candles = await (await fetch(`/api/candles/${coin}?interval=15m&hours=24`)).json();
    if (!S.charts.ap) {
      S.charts.ap = LightweightCharts.createChart($("#ap-chart"), {
        layout: { background: { color: "transparent" }, textColor: "#8B94A3",
          fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
        grid: { vertLines: { color: "#1F2630" }, horzLines: { color: "#1F2630" } },
        timeScale: { timeVisible: true, borderColor: "#1F2630" },
        rightPriceScale: { borderColor: "#1F2630" }, autoSize: true });
      S.charts.ap.addCandlestickSeries({
        upColor: "#26A69A", downColor: "#EF5350", borderVisible: false,
        wickUpColor: "#26A69A", wickDownColor: "#EF5350" });
    }
    S.charts.ap.series()[0].setData(candles.map(c => ({
      time: Math.floor(c.t / 1000), open: +c.o, high: +c.h, low: +c.l, close: +c.c })));
    S.charts.ap.timeScale().fitContent();
    const book = await (await fetch(`/api/l2book/${coin}`)).json();
    const lv = book.level || book.levels || [];
    const bids = (lv.find(l => l.side === "B")?.levels || []).slice(0, 6);
    const asks = (lv.find(l => l.side === "A")?.levels || []).slice(0, 6).reverse();
    $("#ap-book").innerHTML =
      asks.map(l => `<div class="r a"><span>${px(l.px)}</span><span>${num(l.sz, 3)}</span></div>`).join("") +
      bids.map(l => `<div class="r b"><span>${px(l.px)}</span><span>${num(l.sz, 3)}</span></div>`).join("");
    const fund = await (await fetch(`/api/funding/${coin}`)).json();
    const fdata = (Array.isArray(fund) ? fund : []).map(f => ({
      time: Math.floor(f.time / 1000), value: +(f.fundingRate * 100).toFixed(4) }));
    if (fdata.length) {
      if (S.charts.fund) { S.charts.fund.remove(); }
      S.charts.fund = S.charts.ap.addLineSeries({ color: "#E8B341", lineWidth: 1,
        priceFormat: { type: "price", precision: 4, minMove: 0.0001 } });
      S.charts.fund.setData(fdata);
      S.charts.fund.applyOptions({ priceScaleId: "" });
      S.charts.fund.priceScale().applyOptions({ scaleMargins: { top: .8, bottom: 0 } });
    }
  } catch (e) { $("#ap-mid").textContent = "feed errore: " + e.message; }
}

/* ---------- system ---------- */
function renderSystem() {
  const k = S.k, c = S.cfg;
  $("#sys-health").innerHTML = kv([
    ["Uptime bot", dur(k.uptimeS)], ["Heartbeat", k.heartbeatAgeS == null ? "—" : dur(k.heartbeatAgeS) + " fa"],
    ["WS HL", S.conn.hlws ? "connesso" : "giù"], ["HyPaper", S.conn.hypaper ? "ok" : "non raggiunto"],
    ["9Router", S.conn.router ? "ok (ultimo ciclo)" : "nessuna chiamata recente"],
    ["Cicli totali", k.cyclesTotal || 0], ["Errori 24h", k.errors24 || 0]]);
  $("#sys-cfg").innerHTML = kv([
    ["Modalità", c.mode], ["Modello", c.model], ["Wallet", c.wallet],
    ["Watchlist", (c.watchlist || []).join(", ")], ["Frac base", c.base_frac],
    ["Lev cap", c.lev_cap], ["Max conc.", c.max_concurrent],
    ["DD g/sett", `${c.daily_dd} / ${c.weekly_dd}`], ["Stop ATR×", c.atr_stop_mult],
    ["z min", c.signal_z_min], ["Scan", c.scanIntervalS + "s"], ["WS collect", c.wsCollectS + "s"]]);
  const errs = S.scans.filter(r => r.stage === "error");
  $("#sys-errors").innerHTML = errs.length
    ? errs.slice(0, 10).map(e => `${e.ts} ${esc(e.error || "")}`).join("\n")
    : "nessun errore in 24h.";
  const lv = $("#sys-log"), follow = $("#log-follow").checked;
  const atBottom = lv.scrollTop + lv.clientHeight >= lv.scrollHeight - 30;
  lv.textContent = S.logs.join("\n");
  if (follow || atBottom) lv.scrollTop = lv.scrollHeight;
}

/* ---------- CSV ---------- */
function exportCsv() {
  const rows = filteredTrades();
  const cols = ["id", "coin", "side", "qty", "notional", "entry", "exit", "stop", "pnl",
    "pnlPct", "fee", "confidence", "durS", "closeReason", "status", "tsOpen", "tsClose"];
  const csv = [cols.join(",")].concat(rows.map(t => cols.map(c =>
    JSON.stringify(t[c] ?? "")).join(","))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "trades.csv"; a.click();
}

/* ---------- tabs & events ---------- */
function switchTab(name) {
  S.tab = name;
  $$("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  $$("main section").forEach(s => s.classList.toggle("on", s.id === "tab-" + name));
  ({ overview: renderOverview, trades: renderTrades, analytics: renderAnalytics,
     agents: renderAgents, market: renderMarket, system: renderSystem })[name]?.();
}
document.addEventListener("DOMContentLoaded", () => {
  $$("nav button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
  $("#f-status").onchange = renderTrades; $("#f-outcome").onchange = renderTrades;
  $("#f-coin").oninput = renderTrades;
  $("#btn-csv").onclick = exportCsv;
  $("#tbl-trades").addEventListener("click", e => {
    const tr = e.target.closest("tr.click"); if (tr) toggleTradeRow(tr); });
  $("#tbl-market").addEventListener("click", e => {
    const tr = e.target.closest("tr[data-coin]"); if (tr) openAsset(tr.dataset.coin); });
  $("#mkt-filter").oninput = renderMarket;
  $("#ag-cycle-sel").onchange = e => showCycle(+e.target.value);
  wsConnect();
});
