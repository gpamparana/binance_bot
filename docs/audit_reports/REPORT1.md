# Crypto Futures Bot Functional Audit (Critical Review)

## Scope
- Functional audit of live/paper trading bot behavior, risk controls, execution pipeline, and operational controls.
- Focused on production safety and money-at-risk failure modes.
- Unit test coverage quality was reviewed only as supporting context.

## Executive Summary
The bot has a strong architecture and good modular separation, but there are **critical implementation gaps** between intended safety behavior and what actually runs. The most serious issue is that multiple risk-management controls are implemented but not actually invoked, while several metrics are placeholders in production paths. In its current form, this should **not** be considered production-ready for real-money futures trading.

---

## Critical Findings (Must Fix Before Real Money)

### 1) Risk controls exist but are effectively disconnected from runtime flow
- `_check_circuit_breaker()` and `_check_drawdown_limit()` are implemented but there are no call sites in runtime execution paths. They are referenced in comments only, not actually executed from `on_bar`, order handlers, or a timer loop.
- Consequence: Expected emergency protections can silently never trigger.

### 2) Risk-management configuration is read from wrong object path
- Strategy reads risk values using `getattr(self._hedge_grid_config, "max_errors_per_minute", ...)` and similar patterns, but config schema stores these under `risk_management.*`.
- Consequence: even if risk checks were called, configured thresholds are ignored and defaults are used.

### 3) Order-size validation exists but is never enforced
- `_validate_order_size()` exists but has no call sites.
- It also reads `max_position_pct` from the wrong top-level path instead of `position.max_position_pct`.
- Consequence: there is no effective notional gate before order submission from this strategy.

### 4) Funding guard is mostly inert in live operation
- FundingGuard requires `on_funding_update()` calls to become active.
- Strategy never feeds funding updates into FundingGuard; additionally `_get_current_funding_rate()` returns placeholder `0.0` with TODO marker.
- Consequence: funding risk controls and funding metrics are largely non-functional.

### 5) Operational throttle control is not connected to trading logic
- API control path writes `strategy._throttle` directly.
- Strategy has `set_throttle()` validation method, but ops path bypasses it.
- `_throttle` is not used in order generation/sizing path.
- Consequence: control endpoint gives a false sense of control.

---

## High-Risk Functional Inconsistencies

### 6) Warmup config discovery points to non-existent filenames
- Warmup loader looks for `configs/venues/binance_testnet.yaml` and `configs/venues/binance.yaml`, but repository ships `binance_futures_testnet.yaml` and `binance_futures.yaml`.
- Consequence: warmup can silently fall back to synthetic/minimal config paths unexpectedly.

### 7) Execution config fields are partially unused
- `execution.maker_only` exists in schema but limit-order creation hardcodes `post_only=True`.
- `execution.retry_max_price_deviation_bps` exists in schema but no enforcement use in rejection retry flow.
- Consequence: config implies behavior that is not actually controllable.

### 8) Operational metrics exposed as real-time values include placeholders
- Margin ratio, realized PnL, and funding rate currently return `0.0` placeholders.
- Consequence: dashboards and alerts can appear healthy while hiding real account stress.

---

## Medium Findings

### 9) Strategy comments and behavior diverge in several places
- Comments indicate safety/risk checks are called in trading operations, but call sites are absent.
- Risk/ops code suggests production hardening, but several paths are TODO/placeholder.

### 10) Optimization script sprawl and duplicate intent
- Multiple top-level optimization scripts exist with overlapping logic and inconsistent messaging (e.g., one script runs `n_trials=10` while print says "5 trials").
- Consequence: operator confusion and accidental execution of stale workflows.

---

## Strategy Assessment (Opinionated)

### Strengths
- Clean componentized pipeline: regime detection → ladder construction → policy shaping → funding guard → order diff.
- Good precision guard and ID-parsing mechanics.
- Solid intent around hedge-mode operations and TP/SL attachment lifecycle.

### Weaknesses for production futures deployment
- Real risk controls are not wired into hot paths.
- Monitoring data quality is currently insufficient for 24/7 unattended execution.
- Significant behavior/config drift (what config says vs what code does) increases operational risk.

---

## Recommended Remediation Plan

### Phase 1 (Blocker fixes)
1. Wire risk checks into deterministic runtime cadence:
   - `on_bar` (every bar) for drawdown and guard checks.
   - Error handlers increment dedicated counters and invoke circuit-breaker evaluation.
2. Fix config-path reads:
   - Use `self._hedge_grid_config.risk_management.*` and `self._hedge_grid_config.position.max_position_pct`.
3. Enforce `_validate_order_size()` before `submit_order` on adds/retries and TP/SL submissions.
4. Integrate real funding feed and call `FundingGuard.on_funding_update()`.

### Phase 2 (Safety hardening)
1. Make throttle operational:
   - Use `set_throttle()` in ops API callback.
   - Apply throttle to sizing/policy in order construction path.
2. Align warmup config discovery with actual repo filenames.
3. Remove/implement placeholder metrics before relying on Prometheus alerts.
4. Add fail-closed mode: if critical metrics unavailable, pause trading by policy.

### Phase 3 (Codebase hygiene)
1. Consolidate top-level optimization scripts into one CLI entrypoint with profiles.
2. Archive or remove stale strategy config variants not used by docs/runbooks.
3. Add a production-readiness checklist that is enforced at startup (not just documented).

---

## Production Readiness Verdict
**Current verdict: NOT READY for real-money futures trading.**

The architecture is promising, but until risk controls are actively wired, config/behavior drift is removed, and operational metrics are truthful, this bot should remain in backtest/paper-only environments.
