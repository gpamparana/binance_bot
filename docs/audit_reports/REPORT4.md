# naut-hedgegrid Comprehensive Code Audit (REPORT5)

Date: 2026-02-21
Scope: `naut_hedgegrid/`, `configs/`, `tests/`, `docker/`, `pyproject.toml`, `README.md`

## 1) Architecture & Layered Structure Integrity
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Concrete layering is mostly followed in the strategy orchestrator (`strategies/.../strategy.py` imports domain + strategy components + exchange precision, and delegates pipeline steps in `on_bar`).
  - `strategy/grid.py` and `strategy/policy.py` directly import full config models (`HedgeGridConfig`) instead of narrower DTOs/interfaces, tightening coupling between Component and Config layers.
  - `ops/manager.py` directly accesses strategy internals (`_ladder_lock`, `_last_long_ladder`, `_get_live_grid_orders`) via callback path, which leaks private strategy state into Ops/UI integration.
  - No obvious circular import crashes were found in this snapshot, but package-level `__init__.py` fan-out exports (e.g. runners and strategies) increase cycle risk.
- **Risk Level:** Medium
- **Recommendation:**
  - Introduce minimal protocol-style interfaces for component inputs (`GridParams`, `PolicyParams`) and move private state access behind explicit strategy methods.

## 2) Trading Logic & Grid Engine Correctness
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - `GridEngine.build_ladders()` correctly builds LONG below mid and SHORT above mid, with geometric qty scaling and recenter checks using Decimal arithmetic.
  - `PlacementPolicy.shape_ladders()` ignores `policy.strategy`; both `core-and-scalp` and `throttled-counter` currently run the same throttle path.
  - `RegimeDetector` computes EMA/ADX/ATR and hysteresis correctly, and warmup gate exists (`is_warm`).
  - Funding guard projected cost formula scales by `window / 8h` irrespective of time-to-next-funding and without position size context, which may under/over-throttle.
  - `OrderDiff` uses tolerant matching and precision clamping, but fixed tolerances (1 bps / 1%) can still churn in fast markets.
  - TP/SL order side is opposite side as expected, but both TP and SL are submitted with `reduce_only=False`.
- **Risk Level:** High
- **Recommendation:**
  - Implement distinct logic for `core-and-scalp` in `PlacementPolicy`.
  - Rework funding-cost model to use `position_notional * rate * time_fraction`.
  - Enforce `reduce_only=True` once hedge-mode risk-engine compatibility is solved (or route via exchange-native reduce-only).

## 3) Hedge Mode Position Management
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Position IDs consistently use `{instrument}-LONG/SHORT` in order submit and position reads.
  - `_execute_add()` always passes side-scoped `position_id`.
  - Cache hydration exists and filters strategy orders + TP/SL correctly.
  - OMS type can still become NETTING if venue config sets `trading.hedge_mode=false`; no hard fail guard.
- **Risk Level:** High
- **Recommendation:**
  - Add startup invariant: abort if `oms_type != HEDGING` for this strategy.

## 4) Risk Controls — Hot Path Verification
- **Status:** 🔴 Critical Issue
- **Findings:**
  - Drawdown check is **not at top of every on_bar**; it runs only after detector warmup gate. During warmup, drawdown protection does not execute.
  - Circuit breaker check is called from rejected/denied handlers, but it records timestamps only when called; there is no explicit event classification/severity and no persistence.
  - `_validate_order_size()` is called before `_execute_add()` submits, but it validates single-order notional against free balance, not `current position + proposed order` vs `max_position_pct`.
  - Config access mostly uses nested fields (`risk_management`, `position`) and not root `getattr`, which is good.
  - Manual flatten / emergency close paths bypass `_validate_order_size()` (expected operational path, but still bypasses standard gate).
- **Risk Level:** Critical
- **Recommendation:**
  - Move `_check_drawdown_limit()` to immediately after init checks in `on_bar()`.
  - Rework `_validate_order_size()` to include existing side exposure and pending orders.

## 5) NautilusTrader Strategy Lifecycle Correctness
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Lifecycle handlers are broadly implemented (`on_start`, `on_bar`, `on_data`, fill/accept/cancel/reject/deny, `on_stop`).
  - `on_bar` sequence mostly matches target pipeline, but drawdown gate order differs as noted.
  - `on_stop()` cancels open strategy orders but does not guarantee TP/SL-only cleanup separately.
- **Risk Level:** Medium
- **Recommendation:**
  - Align `on_bar` ordering exactly to documented flow with risk gate first.

## 6) Exchange API & Connectivity Resilience
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Binance operations are routed through Nautilus adapter and TradingNode configs.
  - Testnet monkey patch is applied unconditionally at import time in `base_runner.py`; patch scope is broad.
  - Warmup uses hardcoded config filenames (`binance_testnet.yaml`, `binance.yaml`) that do not match repo files (`binance_futures*.yaml`), increasing fallback behavior.
  - Warmup is intentionally non-blocking (logs warning and returns), which is operationally safe but strategy quality degrades if detector remains cold.
- **Risk Level:** Medium
- **Recommendation:**
  - Gate testnet patch by environment/flag.
  - Fix warmup config discovery paths and surface degraded-start telemetry.

## 7) Operational Controls & Kill Switch
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - `OperationsManager` wires Prometheus + API + KillSwitch when started.
  - KillSwitch flatten path delegates to strategy and handles duplicate-in-flight via lock.
  - Alert failures are handled via `gather(..., return_exceptions=True)` and logged.
  - KillSwitch is instantiated from default `KillSwitchConfig()` in manager, not from runtime config file/env override path.
- **Risk Level:** Medium
- **Recommendation:**
  - Load `KillSwitchConfig` explicitly from operations config for environment parity.

## 8) FastAPI REST API Security
- **Status:** 🔴 Critical Issue
- **Findings:**
  - API auth is optional by design; if `api_key` is unset, all control endpoints are unauthenticated.
  - Server binds to `0.0.0.0` by default.
  - CORS is fully open (`allow_origins=["*"]`, all methods/headers).
  - No explicit API rate limiting.
- **Risk Level:** Critical
- **Recommendation:**
  - Force API key in live mode, bind to localhost by default, add IP allowlist / rate limiting middleware.

## 9) Security Audit
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - No hardcoded exchange keys found in source; YAML supports `${ENV}` expansion with strict missing-var error path.
  - Dependency pins are broad (`nautilus-trader`, `pydantic>=2`, `fastapi>=0.110.0`), but CVE status wasn’t verifiable in this environment due missing installed deps.
  - Dockerfile uses non-root runtime user, but copies `src/` even though repo package root is `naut_hedgegrid/`.
- **Risk Level:** Medium
- **Recommendation:**
  - Add `pip-audit` in CI, pin upper/lower bounds, and fix Docker COPY paths.

## 10) Concurrency & Race Conditions
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Strategy comments assume event-driven safety, but ops API runs in another thread and mutates strategy state via callbacks.
  - Some shared state has locks (`_ladder_lock`, `_grid_orders_lock`, `_fills_lock`), but not all paths are synchronized (e.g., `_throttle` simple atomic float assignment).
  - Parallel optimizer uses process isolation and temp files, reducing shared-memory corruption risk.
- **Risk Level:** Medium
- **Recommendation:**
  - Formalize thread-safety contract for every shared field and centralize mutation through a single-thread mailbox.

## 11) Error Handling & Resilience
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Codebase contains extensive `except Exception` usage, including hot strategy paths.
  - Some catches are fail-safe (good), but others may mask root causes (e.g., broad catches in fill/PnL tracking and retries).
  - Crash recovery has partial support via `_hydrate_grid_orders_cache()` and position reconciliation.
  - Critical state is mostly in-memory; persistence is limited (optimization DB only).
- **Risk Level:** High
- **Recommendation:**
  - Narrow exception classes and attach structured context IDs; persist minimal recovery state for open ladders/exits.

## 12) Code Quality & Cleanliness
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - `strategy.py` is very large and multi-responsibility; complex methods exceed maintainability comfort.
  - Several stubs remain in API semantics (`/start`, `/stop` behavior delegated and may be operational no-op depending on strategy callback implementation).
  - Type hints are generally present, but there are suppressions and broad dynamic typing in ops callback boundaries.
- **Risk Level:** Medium
- **Recommendation:**
  - Split `strategy.py` into execution/risk/position-management service classes.

## 13) Configuration Validation
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Pydantic models have strong field constraints and relationship validators (EMA fast/slow, qty scale bounds, etc.).
  - Venue config defaults `hedge_mode=False`, so hedge mode is not enforced by schema itself.
  - Strategy startup validates configs through loader; validation errors are surfaced.
- **Risk Level:** Medium
- **Recommendation:**
  - Add strategy-level validator requiring hedge mode true for hedge-grid strategy.

## 14) Testing Gaps
- **Status:** 🔴 Critical Issue
- **Findings:**
  - README claims “645 collected / 608 pass / 37 skipped”, but current environment cannot reproduce due missing runtime deps; collection failed early.
  - Explicit skip markers currently found: 9 skip sites (pipeline, parity, ops integration, smoke skip condition).
  - No evidence in this run of dedicated regression test for the known root-`getattr` config bug pattern.
- **Risk Level:** High
- **Recommendation:**
  - Add CI matrix that installs full deps and publishes pass/skip trend; add explicit regression for nested risk config access.

## 15) Data Pipeline Integrity
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Schemas and normalization are robust for common malformed rows.
  - `validate_dataframe_schema()` only validates first 10 rows for performance, so deeper corruption can pass.
  - Timestamp normalization infers unit by first-sample magnitude; mixed-format columns can be misparsed.
- **Risk Level:** Medium
- **Recommendation:**
  - Add full-column sampling strategy (random + boundary rows) and mixed-unit detection guardrails.

## 16) Performance
- **Status:** 🟡 Needs Improvement
- **Findings:**
  - Hot path does substantial per-bar logging and repeated cache scans (`orders_open`) in multiple methods.
  - Several dictionaries/sets are bounded manually, but some histories can still grow between cleanups.
  - Parallel optimizer may over-count duration because `start_time` is captured after future completion dispatch handling.
- **Risk Level:** Medium
- **Recommendation:**
  - Reduce info-level logs in live hot path; centralize order snapshot per bar; instrument timing around diff/execute.

---

## Executive Summary

### Top 5 Critical Issues (fix before live)
1. Risk gate ordering: drawdown protection runs after warmup gate, creating early-session bypass.
2. Position-size validator ignores existing side exposure and pending risk.
3. API can be unauthenticated in live if key unset; service binds publicly.
4. TP/SL/flatten close flows use `reduce_only=False` in hedge mode.
5. Testing posture is unverifiable in current state; README test counts are stale relative to executable environment.

### Top 5 Robustness Improvements
1. Enforce `OmsType.HEDGING` invariant at startup.
2. Replace warmup config path guesses with actual repo filenames and explicit mode selection.
3. Load kill switch config from ops config source, not default constructor.
4. Introduce structured retry/error taxonomy for circuit breaker inputs.
5. Persist minimal strategy recovery state for exits and ladder center.

### Top 5 Code Quality Improvements
1. Decompose `strategies/hedge_grid_v1/strategy.py` into smaller services.
2. Remove private-field coupling from `ops/manager.py` callback operations.
3. Implement true `core-and-scalp` policy branch.
4. Narrow broad exception handlers in hot paths.
5. Add explicit boundary tests for config access and hedge-mode invariants.

### Overall Health Score
**6.2 / 10** — Core design is solid and modular, but there are material production-safety gaps (risk gate ordering, API exposure defaults, reduce-only semantics) that must be closed before live capital.

### Prioritized Action Plan (risk × effort)
1. **P0 (High/Low effort):** Move drawdown check to top of `on_bar()` after init checks.
2. **P0 (High/Low effort):** Require API key + localhost bind by default in live mode.
3. **P0 (High/Medium effort):** Implement exposure-aware `_validate_order_size()` with pending orders.
4. **P1 (High/Medium effort):** Harden reduce-only handling for TP/SL/flatten in hedge mode.
5. **P1 (Medium/Low effort):** Enforce hedge-mode invariant (`hedge_mode=true`) at startup.
6. **P1 (Medium/Low effort):** Fix warmup config filename discovery and emit degraded-start metric.
7. **P2 (Medium/Medium effort):** Split strategy monolith into execution/risk/order modules.
8. **P2 (Medium/Medium effort):** Add CI dependency-complete test lane + skip trend report.
