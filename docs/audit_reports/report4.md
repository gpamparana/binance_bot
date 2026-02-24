# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope: repository-wide static audit of architecture, strategy hot path, ops, API, data, and test posture.
Method: file-by-file source review + targeted commands (`rg`, `pytest -q -rs`).

---

## 1) Architecture & Layered Structure Integrity

**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Layering is mostly respected: strategy orchestrator imports reusable components/domain/exchange/config, while reusable `strategy/*` modules stay independent of runner/UI layers. (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`, `naut_hedgegrid/strategy/*.py`)
- `strategy.py` has become a “god object” (~2700 LOC) with orchestration + lifecycle + risk + exit/OCO + ops bridge, weakening separation of concerns despite good component modules.
- Domain types are broadly used (`Side`, `Regime`, `Ladder`, `Rung`, `OrderIntent`, `LiveOrder`), but some helper APIs still pass raw strings (e.g., ops callback and `flatten_side("long"|"short")`) increasing boundary ambiguity.
- Precision layer is in the right location (`exchange/precision.py`) and used in diff path, but price/qty rounding is duplicated in strategy TP/SL creation with hardcoded decimal quanta (`0.01`, `0.0001` style behavior), bypassing a single precision source of truth.

**Recommendation**
- Split `strategy.py` into focused collaborators:
  - `risk_runtime.py` (drawdown/circuit/size)
  - `exits.py` (TP/SL/OCO/position reconciliation)
  - `ops_bridge.py` (API/metrics flatten/throttle)
- Normalize API/ops boundary types to enums/dataclasses instead of raw strings.
- Route *all* price/qty finalization through `PrecisionGuard` at order construction boundaries.

---

## 2) Trading Logic & Grid Engine Correctness

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- `GridEngine.build_ladders()` correctly places LONG levels below mid and SHORT above mid; geometric sizing is correctly `base_qty * qty_scale^(level-1)` with Decimal arithmetic.
- `recenter_needed()` uses precise bps deviation and initial recenter when `last_center==0` (sane).
- `PlacementPolicy` behavior is consistent with docs for `throttled-counter`; `core-and-scalp` intentionally truncates both sides and scales only counter side.
- `RegimeDetector` uses EMA spread + ADX gate + hysteresis; warmup checks require EMA+ADX warm, reducing early false classification.
- ATR is computed but not used in classification logic (despite requirements/spec emphasis on ATR in regime behavior).
- Funding guard scales exposure close to funding, but projected-cost model is window-based simplification and may under/overestimate real cash impact vs actual holding notional/time-to-funding.
- Diff tolerances default to 1 bps and 1% qty; on noisy markets this can still churn due frequent micro-changes, especially when combined with recenter + policy shifts.
- TP/SL side inversion and position suffixing are mostly correct, but realized PnL computed at TP/SL fill reads *current* position avg open from cache (may be changed/flat by then), risking inaccurate realized PnL accounting.

**Recommendation**
- Add ATR influence explicitly (e.g., dynamic hysteresis or volatility regime overlays).
- Introduce configurable churn guard: min time between replaces per rung + per-bar max replaces.
- Persist entry reference per fill key and compute realized PnL from fill pair, not mutable cache position state.

---

## 3) Hedge Mode Position Management

**Status:** 🟢 Good
**Risk Level:** Medium

**Findings**
- Hedge-mode position IDs consistently follow `{instrument_id}-LONG|SHORT` across grid submissions and TP/SL creation.
- Defensive check enforces `OmsType.HEDGING` at startup and pauses strategy on mismatch.
- `_hydrate_grid_orders_cache()` avoids duplicate ladder placement after restart by loading open orders and filtering out TP/SL IDs.
- LONG/SHORT lifecycles are mostly independent, with separate position IDs and side-aware exit creation.

**Recommendation**
- Add invariant assertions/test hooks that reject any submit path lacking explicit `position_id`.
- Expand reconciliation logic to verify side-specific TP/SL quantity matches current side position quantity.

---

## 4) Risk Controls — Hot Path Verification

**Status:** 🟡 Needs Improvement
**Risk Level:** Critical

**Findings**
- Drawdown check is correctly called at top of `on_bar()` before trading flow.
- Circuit breaker is called from both `on_order_rejected()` and `on_order_denied()` paths.
- Order size validation is invoked in `_execute_add()` before `submit_order()`.
- Drawdown computation currently uses `account.balance_total` only; requirement asked unrealized drawdown against peak equity—implementation may lag true equity stress depending on account update semantics.
- TP/SL attachment path does **not** run through `_validate_order_size` (intentionally reduce-only, but still a bypass relative to “every submit” requirement).
- Manual/API flatten path bypasses drawdown/circuit gate by design (expected for emergency action) but should be explicitly audited with stronger permissions.

**Recommendation**
- Use explicit equity source (balance + unrealized PnL if needed) for drawdown.
- Keep bypass for reduce-only exits, but codify with explicit `risk_bypass="reduce_only_exit"` audit logging.
- Add dedicated tests proving no create-order path can bypass `_validate_order_size` except approved bypass categories.

---

## 5) NautilusTrader Strategy Lifecycle Correctness

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Lifecycle callbacks are implemented comprehensively (`on_start`, `on_bar`, `on_data`, order event handlers, `on_stop`).
- `on_bar` order mostly matches documented pipeline: risk gate → detector → recenter/build/policy/funding/throttle/precision+diff/execute.
- `on_start()` swallows config load exceptions and returns without raising; this can leave actor alive but inert (operational ambiguity).
- Broad exception handlers in event methods may mask actionable failures (some re-raise, some only log).

**Recommendation**
- Fail fast on startup fatal config/instrument errors (`raise RuntimeError`) and surface to runner.
- Narrow exception types in hot handlers; reserve broad catches for top-level safety boundary with explicit error classification.

---

## 6) Exchange API & Connectivity Resilience

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Exchange interactions mostly flow through Nautilus adapter in runner/strategy.
- Testnet monkey patch is only applied when `venue_cfg.api.testnet` is true, lowering prod leak risk.
- Historical data source has retry/backoff and explicit 429 handling in `data/sources/binance_source.py`.
- Warmup is non-blocking in strategy; failures are logged and trading continues. This is operationally safe for liveness but increases initial classification risk (cold detector path).
- No explicit stale-market-data watchdog in live strategy path beyond reliance on incoming bar stream.

**Recommendation**
- Add stale-data guard: if no bar/mark update within threshold, pause new order placement and alert.
- Gate warmup fallback by policy (`allow_cold_start`) and emit warning metric/alert.

---

## 7) Operational Controls & Kill Switch

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- `OperationsManager` correctly wires Prometheus + API + KillSwitch when enabled, with graceful degradation if subsystems fail.
- KillSwitch checks drawdown/funding/margin/loss and triggers flatten with duplicate-trigger suppression.
- `flatten_now()` catches exceptions and returns status dict; this avoids crash but can hide repeated flatten failures unless alerting/monitoring is strict.
- `OperationsManager` currently instantiates `KillSwitch(..., alert_manager=None)`—alerts are not wired by default despite alert subsystem existing.

**Recommendation**
- Wire `AlertManager` from operations config by default.
- Escalate flatten failure from warning to critical alert + backoff retry schedule.

---

## 8) FastAPI REST API Security

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Write endpoints require auth by default (`require_auth=True`); if no API key configured, writes return 403 (good fail-closed default).
- Read endpoints are open when no key configured (intended monitoring tradeoff).
- API is bound to localhost by default in `OperationsManager` and `StrategyAPI.start_server`.
- Basic in-memory rate limiting exists for reads/writes.
- `/start` and `/stop` are exposed though strategy callback currently does not implement operational state changes (returns unknown op/error path), creating confusing security/ops semantics.

**Recommendation**
- Remove or hard-disable unsupported mutating endpoints until fully implemented.
- Add audit log entries for auth failures and all mutating requests with caller IP + endpoint + result.

---

## 9) Security Audit

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- No hardcoded exchange keys found in reviewed code.
- YAML env substitution `${VAR}` and `${VAR:-default}` is implemented.
- Dependency pinning is broad (`>=` ranges) in `pyproject.toml`; no lock-based CVE report was produced in this environment.
- Dockerfile runs as non-root user in runtime stage (good baseline).
- SQLite writes mostly parameterized; one query composes SQL with `validity_filter` constant fragment (safe as coded, but avoidable dynamic SQL pattern).

**Recommendation**
- Add CI `pip-audit`/`safety` and fail on critical CVEs.
- Restrict config file permissions in deployment docs (`chmod 600` for secret-bearing files).
- Replace dynamic SQL fragments with fully parameterized query branching.

---

## 10) Concurrency & Race Conditions

**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Strategy assumes Nautilus actor single-threaded event dispatch; internal locks were added for shared mutable caches.
- API callbacks execute in thread pool and call strategy methods directly; comments claim cache safety, but not all mutable state access is synchronized end-to-end.
- Some shared fields (`_throttle`, `_pause_trading`) are atomic for assignment/read but compound operations still risk races across API/event threads.
- Parallel optimization uses process isolation pattern; no obvious shared-memory corruption in reviewed files.

**Recommendation**
- Introduce a single strategy-command queue for API mutations executed on strategy/event thread.
- Document thread-safety contract explicitly per callable method.

---

## 11) Error Handling & Resilience

**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Many broad `except Exception` catches exist across strategy/ops/data paths.
- Some catches are appropriate fail-safe boundaries (risk checks), but others only log and continue, potentially masking logic bugs.
- Crash recovery support exists (`_hydrate_grid_orders_cache`, position reconciliation), but critical state is mostly in-memory.

**Recommendation**
- Classify exceptions into: retriable/transient, operational warning, and fatal.
- Persist minimal restart-critical state (e.g., fill-key→entry metadata, circuit breaker status) to local durable store.

---

## 12) Code Quality & Cleanliness

**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- `strategy.py` complexity is very high and combines concerns.
- Some docs appear stale relative to implementation (`/start` `/stop` practical behavior, historical references to `_live_orders`).
- Several magic constants exist in hot path defaults (window ns, diagnostics interval, buffer values).
- Type quality is generally good; occasional `# type: ignore` appears in critical flows.

**Recommendation**
- Enforce max-function-length/cyclomatic thresholds and refactor `on_bar` and `on_order_filled` first.
- Replace magic constants with config fields (or module constants with rationale).

---

## 13) Configuration Validation

**Status:** 🟢 Good
**Risk Level:** Medium

**Findings**
- Pydantic v2 models enforce many constraints and cross-field validations.
- Startup config loading/validation exists via config loaders.
- Runner resolves OMS type from venue hedge flag and strategy enforces HEDGING at runtime.
- Environment separation relies on venue config discipline; paper mode warns if non-testnet but does not hard-fail.

**Recommendation**
- Add explicit guardrails: fail paper runner when `testnet=false` unless `--allow-live-endpoints` explicit override.
- Add startup assertion that live runner rejects testnet venue unless explicit allow flag.

---

## 14) Testing Gaps

**Status:** 🔴 Critical Issue
**Risk Level:** Critical

**Findings**
- In this environment, tests cannot be fully executed due missing dependencies (`pydantic`, `numpy`, `nautilus_trader`, `fastapi`, `aiohttp`, etc.).
- Static skip scan found explicit skips in multiple files, but not enough to confirm the reported “37 skipped” claim from your baseline.
- Critical-path tests exist (strategy smoke, grid/policy/detector/funding/order diff), but execution evidence is unavailable here.
- No direct observed test for the known root-level `getattr` risk-config bug pattern.

**Recommendation**
- Re-run full suite in locked CI env and publish skip report artifact (`pytest -q -rs`).
- Add dedicated regression test for wrong config access pattern (root `getattr` vs nested config fields).

---

## 15) Data Pipeline Integrity

**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Schema validation and normalization pipeline is structured and typed.
- Timestamp normalization uses magnitude heuristics; mixed-format columns may still be misclassified in edge cases.
- Dedup and positivity checks are present; corrupted-but-plausible values (e.g., extreme outliers) can still pass without robust anomaly filters.
- Parquet writer integrates trades/mark/funding; funding written as custom parquet path outside Nautilus-native typed object.

**Recommendation**
- Add stricter anomaly filters (max spread jump, max return per bar) and configurable quarantine path.
- Add data quality metrics (gap count, duplicate count, rejection count) exported to ops.

---

## 16) Performance

**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Hot path repeatedly builds/filters ladders and diffs every bar; acceptable for small grids, but churn can rise with large level counts.
- Some repeated cache scans (`orders_open`) occur in multiple handlers.
- Memory has bounded protections in some places (error/rejection sets capped), but other historical lists (funding/diagnostic context) should be reviewed continuously.

**Recommendation**
- Add per-bar profiling telemetry (build, diff, execute latencies).
- Cache frequently reused open-order snapshots once per bar where possible.

---

# Executive Summary

## Top 5 Critical Issues (fix before live)
1. **Test execution is currently blocked by missing runtime dependencies**, so safety claims are unverified in this environment.
2. **Risk-accounting gaps in drawdown/realized PnL semantics** can misstate stress/performance under fast fills.
3. **Strategy monolith complexity** increases probability of subtle lifecycle/risk regressions.
4. **API command thread-to-strategy direct calls** can create race windows without a serialized command bus.
5. **Kill switch alerting not wired by default** in operations manager reduces incident visibility.

## Top 5 Robustness Improvements
1. Add stale-data watchdog and trading pause on data gap.
2. Implement serialized API command queue on strategy thread.
3. Persist minimal restart-critical state for exits/risk.
4. Add churn guard (replace cooldowns/limits).
5. Make warmup cold-start behavior explicitly configurable and observable.

## Top 5 Code Quality Improvements
1. Decompose `strategy.py` by concern.
2. Remove stale docs/stub endpoint ambiguity.
3. Centralize precision handling at order boundaries.
4. Replace magic constants with config.
5. Reduce broad catches and classify exceptions.

## Overall Health Score
**6.8 / 10**
Strong architecture intent and many good controls are present, but production confidence is limited by test execution gaps in this environment, strategy complexity concentration, and a few high-impact risk/ops hardening needs.

## Prioritized Action Plan (risk × effort)
1. **(High risk / Low effort)** Wire alert manager in ops manager; add flatten-failure critical alerts.
2. **(High / Medium)** Add full-suite CI gate with dependency lock + skip-report artifact + config-access regression tests.
3. **(High / Medium)** Refactor risk/exits out of `strategy.py` and add invariants for submit paths.
4. **(High / Medium)** Introduce serialized API command queue.
5. **(Medium / Low)** Add stale-data pause guard + metrics.
6. **(Medium / Medium)** Improve realized PnL and drawdown equity semantics.
7. **(Medium / Medium)** Expand data anomaly checks and quality metrics.
