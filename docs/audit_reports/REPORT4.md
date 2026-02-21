# Crypto Futures Bot Functional Audit (2026-02-20)

## Scope
- Reviewed live/paper runner flow, core strategy orchestration, order lifecycle handling, risk controls, config safety, and packaging/runtime consistency.
- Focused on production safety for real-money deployment rather than test design.

## Executive summary
The bot has a solid high-level architecture and good component separation (grid, policy, funding, diffing), but there are several **high-risk production gaps** that can lead to incorrect risk behavior, duplicate exposure, and misleading controls.

## Critical findings

### 1) Risk management config is effectively disconnected from runtime checks
- `HedgeGridConfig` nests controls under `position` and `risk_management`.
- Strategy runtime checks read values using `getattr(self._hedge_grid_config, "...")` at the root object, so configured limits are not actually applied.
- Affected controls include max position %, max errors/minute, cooldown seconds, and max drawdown %. Defaults are used silently.

Impact:
- Operators may believe strict limits are active when they are not.
- In production this is a safety/compliance issue.

### 2) Circuit breaker and drawdown logic appear implemented but are not invoked in trading loop
- Methods exist (`_check_circuit_breaker`, `_check_drawdown_limit`) but no active invocation in bar/order execution flow.
- This creates a false sense of protection.

Impact:
- Trading continues under stress/error conditions unless manual intervention occurs.

### 3) Exit orders are not reduce-only
- TP/SL and emergency close paths use `reduce_only=False` with comments explaining Nautilus hedge-mode behavior.

Impact:
- Under side/position mismatch conditions, exits can increase or flip exposure.
- This must be mitigated by strict side validation + exchange-side constraints, otherwise loss-amplifying behavior is possible.

### 4) Potential under-hedging on partial fills per level
- Exit dedup uses `fill_key = "{side}-{level}"` and blocks subsequent TP/SL creation for the same level.
- If a rung fills in multiple partials, later partials can be left without proportional exits.

Impact:
- Position can become partially unprotected.

### 5) Existing open grid orders at startup are not reconstructed into internal cache
- Strategy diffing relies on `_grid_orders_cache`; startup path reconciles positions but does not hydrate live grid orders into this cache.

Impact:
- On restart, bot can place duplicate ladder orders before cache converges.

### 6) Funding protection is mostly non-functional in strategy runtime
- Funding guard is applied each bar, but strategy does not show a funding data subscription/update path.
- Operational funding metrics explicitly return placeholders (`0.0`) with TODO notes.

Impact:
- Funding-aware behavior may never trigger in live runs; dashboards can look healthy while missing funding risk.

## High-priority non-critical findings

### 7) Warmup venue config filename mismatch
- Warmup loader checks `configs/venues/binance_testnet.yaml` and `configs/venues/binance.yaml`, while repo ships `binance_futures*.yaml`.

Impact:
- Warmup often falls back to synthetic config path; behavior differs from operator expectation.

### 8) API credential safety guard can be bypassed by default placeholders
- Production venue config defaults credentials to `BACKTEST_MODE` string.
- Live runner only validates non-empty credentials.

Impact:
- "Credential check passed" can happen with invalid placeholder keys.

### 9) Execution config fields are partially dead
- `maker_only` and `retry_max_price_deviation_bps` are defined in config but not consumed in strategy execution logic.

Impact:
- Config suggests controls that do not exist in behavior.

### 10) Operational metrics include placeholders
- Margin ratio and realized PnL are placeholder returns.

Impact:
- Ops dashboards can be materially misleading in production.

### 11) Packaging/test path inconsistency
- `pyproject.toml` points to `src/naut_hedgegrid`, but repo package is at top-level `naut_hedgegrid/`; pytest pythonpath is also set to `src`.

Impact:
- Build/distribution and CI behavior can drift from local development behavior.

## Cleanup / potential dead assets
1. Root scripts with no code references (`debug_backtest.py`, `run_optimization.py`, `run_optimization_overnight.py`, `run_optimization_one_run.py`) appear ad-hoc/manual.
2. Multiple optimization-specific strategy YAMLs under `configs/strategies/*_best.yaml` appear experiment artifacts; keep if required for reproducibility, otherwise archive.
3. Docs mention `run_optimization_fixed.py`, but there is no central runner registry to indicate canonical entrypoints.

## Strategy opinion (critical)
The strategy is a **mean-reversion grid with trend-aware counter-side throttling**, which can work in stable/high-liquidity regimes but is structurally vulnerable to:
- Persistent one-directional trends,
- Regime lag (EMA/ADX hysteresis),
- Funding drag during sustained imbalance,
- Volatility clustering during macro events.

Without hard, active risk controls and true funding integration, this should **not** be considered production-safe for unattended real-money trading.

## Recommended roadmap (in order)
1. **Wire risk controls for real**: call drawdown/circuit checks on every bar + on rejection bursts; read nested config fields correctly.
2. **Fix exit safety**: enforce reduce-only or equivalent exchange-side position-side guarantees with explicit pre-submit validation.
3. **Handle partial fills correctly**: aggregate open position/filled quantity by side+level and reconcile TP/SL quantity deltas.
4. **Startup reconciliation v2**: reconstruct live grid orders from exchange/cache before first diff cycle.
5. **Funding integration**: subscribe to funding updates, call funding guard updates, remove placeholder metrics.
6. **Config honesty**: delete or implement dead config fields (`maker_only`, retry deviation guard).
7. **Packaging hardening**: align `pyproject` paths with actual package layout; add CI smoke for install/run.
8. **Operational truthfulness**: mark placeholder metrics explicitly as unavailable in API/prometheus.

## Production gating checklist (must pass before go-live)
- [ ] Live dry-run on testnet with forced partial-fill scenarios validates TP/SL coverage.
- [ ] Restart test with open orders confirms no duplicate ladder placements.
- [ ] Circuit breaker and drawdown protections trigger under synthetic fault/load tests.
- [ ] Funding guard activation verified with real funding feed.
- [ ] Invalid credentials fail fast (no placeholders accepted).
- [ ] Dashboards show real values or explicit `N/A` states (not zero placeholders).
