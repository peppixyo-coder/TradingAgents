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
