from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_removed_provider_names_are_absent_from_runtime_and_docs():
    paths = [ROOT / "tradingagents" / "market_intelligence", ROOT / "dashboard", ROOT / "docs", ROOT / "tests"]
    files = [p for root in paths for p in root.rglob("*") if p.is_file() and p != Path(__file__)
             and p.suffix in {".py", ".js", ".html", ".css", ".md", ".example"}]
    text = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert "Fin" + "cept" not in text
    assert "FIN" + "CEPT" not in text
    assert "manual" + "_export" not in text


def test_market_intelligence_routes_are_get_only_and_desk_is_localstorage_only():
    server = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
    routes = ["/api/market-intelligence/health", "/api/market-intelligence/sources",
              "/api/market-intelligence/snapshot", "/api/market-intelligence/export",
              "/api/ohlcv/{coin}"]
    assert all(f'@app.get("{route}")' in server for route in routes)
    assert '@app.post("/api/market-intelligence' not in server
    assert '@app.post("/api/ohlcv' not in server
    js = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'hl-paper-desk:advanced-view' in js
    assert 'localStorage.setItem' in js
