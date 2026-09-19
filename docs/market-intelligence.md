# Optional read-only market intelligence

This feature adds bounded, read-only external snapshots without changing Hyperliquid paper execution, risk, wallet, scan interval, or state persistence.

## Defaults and activation

All features are disabled by default:

```dotenv
HL_EXTERNAL_DATA_ENABLED=false
HL_EXTERNAL_CONTEXT_ENABLED=false
OPENBB_ENABLED=false
FINCEPT_ENABLED=false
HL_EXTERNAL_CONTEXT_MAX_TOKENS=1500
HL_EXTERNAL_DATA_TIMEOUT_S=5
HL_EXTERNAL_DATA_CACHE_TTL_S=300
HL_EXTERNAL_SNAPSHOT_FILE=
```

The normal bot starts without OpenBB or Fincept installed. Do not place API keys in `.env.example`, fixtures, logs, prompts, or commits. Configuration logs may report only configured/not configured.

## Contract and trust boundary

`tradingagents.market_intelligence.contract.ExternalSnapshot` validates version 1 snapshots. It preserves explicit zero values, timestamps, freshness, status, quality and provenance. Payloads are JSON-safe, bounded to 64 KiB, limited in depth/list size, and credential-shaped text is redacted. External text is untrusted data, never an instruction.

Advisory LLM context is bounded and delimited with `BEGIN EXTERNAL MARKET DATA — UNTRUSTED READ-ONLY CONTEXT`; T68b prompt clipping remains the final budget boundary. A provider failure returns `unavailable` and cannot create or modify orders.

## OpenBB

OpenBB ODP documents Python reference groups for crypto, equity, news, technical and economy data. The project is AGPL-3.0 and installation is optional. Provider availability, authentication, rate limits, license and price vary by selected provider; no free/keyless provider is assumed or enabled by default. `OpenBBAdapter` lazy-imports OpenBB only when enabled and accepts a bounded mock/provider boundary for tests.

## Fincept

Fincept Terminal was verified as a C++20/Qt6 desktop modular monolith with internal DataHub/MCP/Python/HTTP/WS adapters. A public live bridge suitable for this repository was not verified. Therefore this project does **not** claim live Fincept integration, does not scrape its UI, reverse-engineer IPC, or copy its code/assets/trade dress. `FinceptAdapter` supports only an explicit, local JSON/CSV export boundary when enabled; otherwise it reports `unavailable`/`bridge unavailable`.

The Fincept repository license is AGPL-3.0 with additional commercial/internal-use restrictions, separate data/API terms, and trademark/trade-dress restrictions. Consult legal counsel before any non-personal use of Fincept software or data services.

## Dashboard

The existing read-only FastAPI dashboard exposes GET-only routes:

- `GET /api/market-intelligence/health`
- `GET /api/market-intelligence/sources`
- `GET /api/market-intelligence/snapshot?asset=BTC`
- `GET /api/market-intelligence/export`

Responses include provider, timestamps, quality, freshness, status and sanitized errors. No POST/PATCH/DELETE endpoint is added. The dashboard does not write `bot.db` or `/app/state`.

## Testing and limits

Tests use temporary files and provider mocks; no live provider, API key, paper wallet, order endpoint or operational state is required. Coverage includes malformed/empty/stale/oversized responses, timeout/error isolation, disabled adapters, JSON/CSV imports, secret redaction, context bounds and read-only routes. This is not a claim of institutional-grade data completeness or execution quality. Do not evaluate profitability until a sufficiently large set of closed trades has real fill and fee reconstruction.

To disable completely, leave all flags false and unset `HL_EXTERNAL_SNAPSHOT_FILE`; remove any optional OpenBB package from a separate environment. Do not modify the paper bot state or historical phantom flags to disable this feature.
