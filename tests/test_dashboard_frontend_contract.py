import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_advanced_desk_uses_existing_chart_vendor_and_indicator_endpoint():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert "vendor/lightweight-charts.standalone.production.js" in html
    assert "/api/indicators/${encodeURIComponent(coin)}" in js
    assert "desk-timeframe" in html and 'value="1h"' in html and 'value="4h"' in html and 'value="1d"' in html
    assert "addCandlestickSeries" in js and "addHistogramSeries" in js


def test_frontend_is_same_origin_get_only_and_accessible():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert 'role="img"' in html and 'aria-label="Candlestick chart' in html
    assert "Recent OHLCV values" in html
    assert "fetch(\"http" not in js and "fetch('http" not in js
    assert "/api/indicators/" in js
    assert "POST" not in js and "PUT" not in js and "DELETE" not in js


def test_tab_navigation_toggles_matching_section_and_keeps_api_contract():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    buttons = [b.split('data-tab="')[1].split('"')[0] for b in re.findall(r'<button data-tab="[^"]+"', html)]
    # ogni bottone nav deve avere una sezione id="tab-<name>" nel main
    for name in buttons:
        assert f'<section id="tab-{name}"' in html, f"missing section for tab {name}"
    # switchTab deve togglare BOTH nav buttons AND sections
    for hay in ('$$("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === name))',
                '$$("main > section").forEach(s => s.classList.toggle("on", s.id === "tab-" + name))'):
        assert hay in js, f"switchTab missing: {hay}"
    # nessuna azione trading e API invariata
    assert "/api/indicators/" in js
    assert "POST" not in js and "PUT" not in js and "DELETE" not in js
    # il CSS nasconde le section senza .on e mostra quelle con .on
    css = (ROOT / "dashboard/static/style.css").read_text(encoding="utf-8")
    assert "section{display:none}section.on{display:block}" in css.replace(" ", "")


def test_analytics_chart_is_robust_to_empty_null_and_malformed_series():
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    # normalizzazione serie: coppie [t, v] -> {x, y} per datetime
    assert "raw.filter(p => Array.isArray(p) && p[0] != null && p[1] != null" in js.replace(" ", "") \
        or "raw.filter(p=>Array.isArray(p)&&p[0]!=null&&p[1]!=null" in js.replace(" ", "")
    # scarto null/NaN
    assert ".filter(p => Array.isArray(p) && p[0] != null && p[1] != null && !Number.isNaN(p[1]))" in js \
        or ".filter(p=>Array.isArray(p)&&p[0]!=null&&p[1]!=null&&!Number.isNaN(p[1]))" in js
    # serie tutte vuote -> emptyChart (niente crash Apex)
    assert "if (normal.every(s => !s.data.length)) return emptyChart(id)" in js
    # formatter y-axis safe per null/NaN (a-capo tollerato)
    flat = re.sub(r"\s+", " ", js)
    assert "v == null || Number.isNaN(v) ?" in flat and "—" in js
    # nessun endpoint di trading introdotto e API invariata
    assert "/api/indicators/" in js
    assert "POST" not in js and "PUT" not in js and "DELETE" not in js


def test_advanced_desk_chart_exposes_native_interactions_and_reset_control():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert 'aria-label="Reimposta vista"' in html
    assert "crosshair: { mode:" in js
    assert "handleScroll" in js and "handleScale" in js
    assert "timeScale().fitContent()" in js
    assert "desk-chart-reset" in js
    assert "S.charts.desk" in js
    assert "/api/indicators/" in js
    assert "POST" not in js and "PUT" not in js and "DELETE" not in js
