# OpenBB / Massive market intelligence integration

## Scope

- Trading loop remains paper-only; order authority remains `executor.py`.
- External data is advisory/read-only, bounded, timestamped and provenance-aware.
- OpenBB is lazy and optional; no mandatory dependency is added.
- Massive is an optional REST provider boundary, disabled by default; symbol coverage is not assumed.
- Dashboard data routes remain GET-only; UI workspace persistence, if added later, must use separate non-operational storage.
- No provider imports executor/store mutators, writes `/app/state`, or changes risk/sizing/order semantics.

## Contract

Version 1 snapshots contain schema version, asset identity, provider, fetched/as-of timestamps, status (`ok|partial|stale|unsupported|unavailable|error`), bounded data, quality and redacted provenance. Missing values are not zero. External text is untrusted data and never an instruction. T68b clipping remains authoritative.

## Provider findings

OpenBB official docs expose Python reference groups for crypto, equity, news, technical and economy data. The project is AGPL-3.0; provider authentication, pricing and limits vary, so no free/keyless provider is assumed.

Massive official docs expose REST, WebSocket and flat-file access, including a crypto documentation section. Pricing distinguishes subscriptions, specialized datasets and paid partner data; API key, rate limit and asset coverage are plan-dependent. No request is made by default and Hyperliquid/Massive symbol compatibility must be verified per asset.

## Verification boundary

Provider failures are isolated per asset/provider with bounded timeout/cooldown and statuses `unsupported`, `unavailable` or `stale`. Tests use fixtures/temp files only. No operational paper state, wallet, balance, position, order, database or phantom flag is touched.

## Licensing

TradingAgents remains Apache-2.0. OpenBB AGPL-3.0 and Massive API/provider terms are documented as external constraints; no external source/assets are copied.

## Final audit — draft remains open

Branch and PR head are synchronized at `a95f8d0135771821cf3bca7b11d438b438bb83e2`; PR #1 remains draft. Fincept/manual-export references are removed from versioned runtime/docs/tests. The operational bot remained paper/healthy with wallet `spike-agent-01`, interval 1800, and no state mutation.

Verified implementation: Advanced Desk tab with Hyperliquid watchlist, search, sorting, selection, provenance text, keyboard focus hooks, localStorage view preferences and responsive CSS. OpenBB/Massive are disabled boundaries; no live external API key/path was verified. Focused scope/provider tests pass; isolated Docker builds and Compose config pass.

Explicitly deferred/not implemented: resizable panels, named server-persisted workspaces, complete OHLCV indicator suite, support/resistance/pivots/manual levels, normalized multi-asset comparison, indicator overlays and complete live browser/API review. Existing Market tab retains its prior candlestick/funding path; Advanced Desk marks OHLCV-dependent indicators unavailable rather than fabricating data.

Current full suite is `680 passed, 2 skipped, 8 failed`; parent `origin/main` reproduces 6 failures, while two T68 regression expectations were corrected to cover the intended bounded hook and initialized client. Full repository Ruff remains non-green with 77 legacy findings; new market-intelligence modules pass Ruff. PR remains draft and not merge-ready until deferred acceptance criteria and global gates are resolved.
