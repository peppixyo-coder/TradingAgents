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
    assert "/api/indicators/" in js


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
    assert "/api/indicators/" in js
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
    assert "/api/indicators/" in js


def test_advanced_desk_chart_exposes_native_interactions_and_reset_control():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert 'aria-label="Reimposta vista"' in html
    assert "crosshair: { mode:" in js
    assert "handleScroll" in js and "handleScale" in js
    assert "timeScale().fitContent()" in js
    assert "desk-chart-reset" in js
    assert "S.charts.desk" in js


def test_advanced_desk_multi_asset_comparison_contract_is_bounded_and_safe():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert 'id="desk-compare"' in html
    assert 'id="desk-compare-chart"' in html
    assert 'id="desk-compare-table"' in html
    assert 'Indice relativo, base 100' in html
    assert 'desk-compare-reset' in html
    assert 'desk-compare-select' in js
    assert 'Math.min(3' in js or 'slice(0, 3)' in js
    assert 'p ? p.value : "—"' in js or 'p ? p.value : null' in js
    assert 'method: "GET"' not in js or "/api/indicators/" in js


def test_workspace_controls_use_only_workspace_api_and_w1_payload():
    html = (ROOT / "dashboard/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    for control in ("workspace-list", "workspace-name", "workspace-create", "workspace-save", "workspace-delete"):
        assert f'id="{control}"' in html
    assert 'aria-live="polite"' in html
    assert "workspacePayload" in js
    assert "workspace_id" not in js.split("function workspacePayload", 1)[-1].split("}", 1)[0]
    assert "created_at" not in js.split("function workspacePayload", 1)[-1].split("}", 1)[0]
    assert '"/api/workspaces"' in js
    assert 'method: "POST"' in js and 'method: "PUT"' in js and 'method: "DELETE"' in js


def test_workspace_ui_has_no_autosave_and_handles_revision_errors():
    js = (ROOT / "dashboard/static/app.js").read_text(encoding="utf-8")
    assert "workspaceSave" in js and "workspaceLoad" in js and "workspaceDelete" in js
    assert "If-Match" in js
    assert "workspace_storage_unavailable" in js
    assert "workspace_conflict" in js
    assert "localStorage" in js  # existing search/sort preferences remain separate
    assert "workspace" not in js.split("localStorage.setItem", 1)[-1].split("}", 1)[0].lower()
