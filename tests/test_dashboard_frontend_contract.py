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
