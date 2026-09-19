# Optional read-only market intelligence

This feature adds bounded external snapshots without changing Hyperliquid paper execution, risk, wallet, scan interval, or state persistence.

## Defaults

```dotenv
HL_EXTERNAL_DATA_ENABLED=false
HL_EXTERNAL_CONTEXT_ENABLED=false
OPENBB_ENABLED=false
MASSIVE_ENABLED=false
MASSIVE_API_KEY=
MASSIVE_API_BASE_URL=https://api.massive.com
MASSIVE_TIMEOUT_S=5
MASSIVE_CACHE_TTL_S=300
```

The normal bot starts without OpenBB or Massive installed. Keys never belong in examples, fixtures, logs, prompts, or commits.

## Contract and trust boundary

`ExternalSnapshot` validates schema version 1, bounded JSON, timestamps, freshness, quality and provenance. Status values include `ok`, `partial`, `stale`, `unsupported`, `unavailable`, and `error`. Missing data is never represented as zero. External text is untrusted data, never an instruction.

Advisory LLM context is bounded and delimited with `BEGIN EXTERNAL MARKET DATA — UNTRUSTED READ-ONLY CONTEXT`; T68b clipping remains authoritative. Provider failures cannot create or modify orders.

## OpenBB

OpenBB ODP documents Python reference groups for crypto, equity, news, technical and economy data. Installation remains lazy and optional. OpenBB is AGPL-3.0; provider authentication, rate limits, licenses and pricing vary, so no free/keyless provider is assumed or enabled by default.

## Massive

Massive official documentation exposes REST, WebSocket and flat-file access, including a crypto documentation section. Pricing distinguishes subscriptions, specialized datasets and paid partner data; API key, rate limit and asset coverage are plan-dependent. The adapter makes no request by default and requires an explicit symbol mapping/fetcher. Hyperliquid compatibility is not assumed. Unsupported mappings return `unsupported`, missing data returns `unavailable`, and stale snapshots remain `stale`.

## Dashboard

The existing read-only FastAPI dashboard exposes GET-only routes:

- `GET /api/market-intelligence/health`
- `GET /api/market-intelligence/sources`
- `GET /api/market-intelligence/snapshot?asset=BTC`
- `GET /api/market-intelligence/export`

No trading mutation endpoint is added. The dashboard does not write `bot.db` or `/app/state`.

## Testing and limits

Tests use temporary files and provider mocks; no live provider, API key, paper wallet, order endpoint or operational state is required. This feature does not claim institutional-grade data completeness or execution quality. Profitability must be evaluated only after enough closed trades have real fill and fee reconstruction.
