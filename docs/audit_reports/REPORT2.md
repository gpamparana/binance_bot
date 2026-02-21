# Code Audit Report — Crypto Futures Trading Bot

Date: 2026-02-20
Scope: Functional audit of live/paper/backtest trading paths, strategy safety controls, config integrity, and repository hygiene.

## Executive Verdict

**Current state is not production-safe for real money.**
The core strategy is feature-rich, but several critical safeguards are either miswired, never invoked, or implemented as placeholders. There are also packaging and operational inconsistencies that can hide defects until runtime.

---

## Critical Findings (P0/P1)

### 1) Risk controls are defined but effectively not enforced (P0)

- The strategy has `_check_circuit_breaker`, `_check_drawdown_limit`, and `_validate_order_size`, but these functions are not called in the active order-execution path.
- `_execute_add()` submits orders directly without size/risk validation.
- Result: strategy can keep placing orders even if error rate spikes or drawdown breaches intended limits.

## Evidence
- Risk functions exist. (`strategy.py` lines ~2221+).
- `_execute_add` path does not call them before `submit_order`.

### 2) Risk configuration is read from wrong object fields (P0)

- `HedgeGridConfig` stores risk values under nested models (`position.*`, `risk_management.*`), but strategy reads top-level attributes with `getattr(self._hedge_grid_config, "max_drawdown_pct", ...)` and similar patterns.
- Result: configured risk limits are silently ignored and fallback defaults are used.

## Evidence
- Nested config schema in `config/strategy.py`.
- Top-level `getattr` lookups in `strategy.py` for max drawdown/circuit breaker/position pct.

### 3) Funding guard logic is never fed with live funding updates (P1)

- `FundingGuard.adjust_ladders()` is called every bar, but `FundingGuard.on_funding_update()` is never called anywhere in strategy lifecycle.
- `adjust_ladders` returns unchanged ladders when no funding data is present.
- Result: funding protection is functionally inactive in production behavior.

### 4) Key operational metrics are placeholders (P1)

- `_get_current_funding_rate`, `_get_realized_pnl`, and margin ratio logic return placeholder defaults (mostly `0.0`).
- Prometheus/API surfaces can therefore report misleadingly safe values.
- Result: operators may trust metrics that are not connected to real account state.

### 5) “Paper trading” default can point to live venue config (P1)

- `run_paper` defaults to `configs/venues/binance_futures.yaml` (testnet false) instead of the testnet file.
- A warning exists, but default remains hazardous for users expecting safe paper mode.

---

## High/Medium Findings (P2)

### 6) Order-state reconciliation depends on in-memory cache events only

- Diffing relies on `_grid_orders_cache`, populated primarily from `on_order_accepted` events in current process lifetime.
- On restart, existing exchange open orders are not explicitly hydrated into this cache before diffing.
- Result: potential duplicate intent generation or stale-state drift after process restarts.

### 7) Warmup config discovery paths don’t match current repo naming

- Warmup tries `configs/venues/binance_testnet.yaml` and `configs/venues/binance.yaml`, but repo uses `binance_futures*.yaml` names.
- Fallback path works, but this indicates config drift and makes behavior less predictable.

### 8) Execution config flags are partially unused

- Example fields such as `maker_only`, `retry_max_price_deviation_bps`, and some risk toggles are defined but not fully enforced in strategy execution logic.
- Result: user assumes controls are active when they are not.

### 9) Packaging/build layout is inconsistent (can block reliable CI/release)

- `pyproject.toml` points pytest `pythonpath` and wheel package path to `src/...`, but repository code is located at `naut_hedgegrid/...` and there is no `src/` directory.
- Makefile typecheck target also points to `src/`.
- Result: broken packaging pipelines, partial test/typecheck coverage, and false confidence from tooling.

---

## Repository Hygiene / Potential Cleanup

The following top-level scripts appear redundant/inconsistent and are not referenced in active CLI/docs pathways:

- `run_optimization.py`
- `run_optimization_one_run.py`
- `run_optimization_overnight.py`
- `debug_backtest.py`

Observations:
- They overlap heavily, contain conflicting comments/parameters (e.g., says “5 trials” while configured otherwise), and rely on ad-hoc `sys.path` insertion.
- Keep one canonical optimization entrypoint (ideally the CLI in `naut_hedgegrid/optimization/cli.py`) and archive/remove the rest.

---

## Strategy Critique (Trading Logic)

Strengths:
- Clear architecture separation (regime, grid, policy, funding guard, diff engine).
- Defensive order-ID handling and retry attempts for post-only rejections.
- Hedge-mode position-side handling is explicit.

Concerns:
- Grid mean-reversion strategy in perpetual futures is highly regime-sensitive and can suffer prolonged one-sided inventory accumulation in trends.
- Current policy throttling helps, but hard stop behavior still depends on TP/SL assumptions and restart correctness.
- Recenter + re-laddering can repeatedly “re-price risk” without explicit portfolio-level VaR/volatility cap.

---

## Priority Fix Plan

1. **Wire risk checks into execution path (before every submit/cancel cycle).**
   - Enforce `enable_*` toggles from `risk_management`.
   - Block new adds when paused/critical/circuit-breaker active.

2. **Fix config field access to nested models.**
   - Replace top-level `getattr` on `HedgeGridConfig` with explicit nested reads:
     - `cfg.position.max_position_pct`
     - `cfg.risk_management.max_errors_per_minute`, etc.

3. **Implement funding event ingestion end-to-end.**
   - Subscribe to funding data and call `FundingGuard.on_funding_update(...)`.
   - Add invariant tests that prove ladders shrink in high-cost windows.

4. **Replace placeholder metrics with authoritative account/trade-based implementations.**
   - Realized/unrealized PnL from closed/open positions or portfolio ledger.
   - Margin ratio from actual account fields.

5. **Make paper mode safe-by-default.**
   - Default `run_paper` to `binance_futures_testnet.yaml`.
   - Hard-fail paper mode if `testnet=false` unless explicit override flag.

6. **Fix packaging/tooling layout now.**
   - Align pyproject + make targets with actual package path.
   - Ensure CI installs dependencies and can run baseline tests/lint/typecheck.

7. **Consolidate optimization scripts into one canonical workflow.**

---

## Suggested Additional Production Guards

- Global max notional per side and global gross notional cap.
- Max active orders cap per side and per minute.
- Exchange connectivity watchdog: auto-pause when stale data/execution heartbeat.
- Restart reconciliation mode: hydrate and normalize all exchange open orders/positions before first trading decision.
- Emergency flat command that confirms completion via exchange state, not only local cache.

---

## Final Opinion

The codebase has good structure and ambition, but **the current integration gaps in risk enforcement and state truthfulness make it unsafe for production capital**. The fastest path to production readiness is not adding new strategy complexity — it is tightening correctness and fail-safe behavior in execution, risk, and operations.
