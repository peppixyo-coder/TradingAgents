# OpenBB / Fincept market intelligence integration

## Scope and current architecture

- Trading loop: `tradingagents/hyperliquid/loop.py`; order authority remains `executor.py` and `run_cycle`.
- Operational state: `TradingAgents/state` bind-mounted to `/app/state`; never used by external adapters for writes.
- Dashboard: separate FastAPI process in `dashboard/server.py`, already read-only over bot state, HyPaper and public/mirror market reads.
- LLM prompt boundary: `NormalizedChatOpenAI._get_request_payload()` and T68b `_fit_prompt_budget`; external context must be bounded before this boundary.
- Existing dashboard contract: WebSocket metrics plus GET routes; API-key middleware is optional via `DASHBOARD_API_KEY`.

## Proposed architecture

1. Add a small stdlib-only `tradingagents/market_intelligence/` package.
2. Define a versioned, bounded `ExternalSnapshot` JSON contract with provider, timestamps, quality, provenance and redacted errors.
3. Add provider interfaces that return snapshots only. No executor, store mutation, wallet, balance or order methods are imported.
4. OpenBB is a lazy optional adapter. The normal bot install and startup do not import or require `openbb`.
5. Fincept is a disabled-by-default boundary. No public documented bridge was verified from the repository architecture: Fincept is a C++20/Qt6 desktop modular monolith with internal DataHub/MCP/Python/HTTP/WS adapters. No UI scraping or reverse engineering is allowed. A documented JSON/CSV import path may be supported without claiming live Fincept integration.
6. Dashboard endpoints are GET-only and consume fixture/state snapshots; no new writer is introduced.
7. LLM enrichment is advisory, compact, untrusted data delimited from instructions, and disabled by default. T68b clipping remains authoritative.

## Provider findings

| Provider | Candidate data | Key | Cost/rate limit | Decision |
|---|---|---:|---|---|
| Hyperliquid/HyPaper | mark, funding, OI, positions, native market context | no external key in adapter | existing local API limits | existing source remains primary |
| OpenBB ODP | documented crypto/equity/news/technical/economy reference groups | provider-dependent | varies by provider; not assumed free | optional lazy boundary only; no provider is enabled by default |
| Fincept Terminal | desktop DataHub/MCP ecosystem; no verified public bridge for this repo | likely required for Fincept services | licensing/API terms apply | boundary/import unavailable by default |
| JSON/CSV manual export | explicit read-only snapshot import | no | local file only | supported fixture/import path, bounded and validated |

OpenBB installation is documented as `pip install openbb`, but the upstream repository is AGPL-3.0 and provider/API terms vary. No OpenBB dependency is added to the core runtime in this increment.

Fincept repository license is AGPL-3.0 with additional commercial/internal-use restrictions, separate data/API terms, and trademark/trade-dress restrictions. No Fincept source, asset, logo, UI layout, or code is copied.

## Data contract

```json
{
  "schema_version": 1,
  "snapshot_id": "deterministic-or-uuid",
  "asset": "BTC",
  "canonical_asset": "BTC",
  "fetched_at": "ISO-8601 UTC",
  "as_of": "ISO-8601 UTC or null",
  "provider": "hyperliquid|openbb|fincept|manual_export",
  "status": "ok|partial|stale|unavailable|error",
  "data": {},
  "quality": {"freshness_seconds": 0, "source": "", "coverage": "full|partial|none", "errors": []},
  "provenance": {"endpoint_or_query": "redacted", "license": "", "requires_api_key": false, "paid": false}
}
```

Limits: bounded JSON size, bounded text/list fields, UTC timestamps, explicit missing-vs-zero semantics, sanitized errors, no secrets/prompts/LLM output. External text is untrusted data and never an instruction.

## Risks and mitigations

- Supply-chain/licensing: no mandatory OpenBB/Fincept dependency; document licenses and keep adapters isolated.
- Prompt injection: external text is escaped/length-limited/delimited and advisory-only.
- SSRF/secrets: URLs are configuration-only, not user-controlled; credentials are presence-only in logs.
- Rate limits/outages: timeout, TTL cache, bounded cooldown, per-asset isolation, unavailable snapshots.
- Trading authority: adapters expose no executor/store mutator and static tests assert no order symbols.
- Staleness: every snapshot carries `fetched_at`, `as_of`, freshness and status.

## Test and success criteria

- Contract validation rejects malformed, oversized, stale or credential-bearing payloads.
- OpenBB absent/present paths are both safe; Fincept unconfigured/import malformed paths are safe.
- Provider timeout/503/rate-limit cannot block another asset or create an order.
- T68b clipping and critical fields remain intact.
- Dashboard GET APIs expose provenance and stale/unavailable state without POST/PATCH/DELETE trading routes.
- Tests use temporary directories, mocks and fixture data only; no operational `TradingAgents/state` volume or paper bot start.
- Feature defaults disabled and bot behavior unchanged.

## Not verified

- No free, keyless OpenBB provider was assumed without a provider-specific terms/rate-limit verification.
- No public Fincept bridge endpoint/file protocol was verified; live Fincept integration is not claimed.
- Institutional-grade execution, data completeness, or commercial licensing suitability is not claimed.
