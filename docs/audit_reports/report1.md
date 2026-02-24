# naut-hedgegrid Comprehensive Code Audit (NautilusTrader >=1.220.0)

Date: 2026-02-21
Scope reviewed: strategy orchestration, reusable strategy components, domain, exchange precision, config, runners, ops, ui api, warmup/data pipeline, optimization, tests, docker, dependencies.

## 1) Architecture & Layered Structure Integrity
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Layering is mostly respected (orchestrator in `strategies/hedge_grid_v1/strategy.py`, reusable logic in `strategy/*`, domain primitives in `domain/types.py`).
- `strategy.py` directly imports many lower-level modules and owns substantial non-orchestration logic (order ID parsing cache, metrics, flattening, retry policy, warmup orchestration), causing a “god object” drift.
- `ops/manager.py` and `ui/api.py` operate on strategy internals (`_throttle`, snapshots) through callback surface rather than strict interface contracts.

**References:**
- `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py` (component init and orchestration).
- `naut_hedgegrid/strategy/*.py` (reusable logic).
- `naut_hedgegrid/ops/manager.py` callback bridge.

**Recommendation**
- Introduce a narrow `StrategyControlPort` protocol for ops/api with explicit methods (flatten, set_throttle, snapshots) and avoid direct internals.
- Split `strategy.py` into lifecycle orchestrator + execution service + risk service.

---

## 2) Trading Logic & Grid Engine Correctness
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- `GridEngine.build_ladders()` correctly places LONG rungs below mid and SHORT above mid; geometric scaling (`qty_scale ** (level-1)`) is correct.
- `recenter_needed()` uses basis-point deviation and is reasonable.
- `RegimeDetector` computes EMA/ADX/ATR and applies hysteresis; warmup gating prevents trading until warm.
- **Important:** ATR is computed but not used for classification, despite architecture docs suggesting volatility-aware regimeing.
- `FundingGuard` uses funding rate and next funding timestamp, scales paying side near funding; projected cost is linearized over an 8h period.
- `OrderDiff` tolerance defaults (1 bps price / 1% qty) can be too tight for noisy fast markets, likely increasing churn.
- TP/SL attachment uses opposite side and hedge-side position IDs, but TP/SL are submitted with `reduce_only=False` due Nautilus hedge-mode risk engine behavior; this is operationally risky if `position_id` routing ever drifts.

**References:**
- `naut_hedgegrid/strategy/grid.py`
- `naut_hedgegrid/strategy/policy.py`
- `naut_hedgegrid/strategy/detector.py`
- `naut_hedgegrid/strategy/funding_guard.py`
- `naut_hedgegrid/strategy/order_sync.py`
- `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`

**Recommendation**
- Make order-diff tolerances configurable per symbol volatility.
- Add optional ATR-aware regime gates or remove ATR complexity if intentionally unused.
- Add hard invariant checks before TP/SL submit: side/opposite-side/position_id consistency.

---

## 3) Hedge Mode Position Management
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Hedge-mode IDs are consistently built as `{instrument_id}-LONG|SHORT` in add/TP/SL/reconciliation paths.
- `on_start()` enforces `OmsType.HEDGING` and pauses trading on mismatch.
- `_hydrate_grid_orders_cache()` reconstructs grid state from open orders and ignores TP/SL IDs; good restart behavior.
- Residual risk: any malformed client IDs in cache are logged and skipped (safe), but can cause temporary under-tracking.

**Recommendation**
- Add startup assertion that venue config hedge_mode and strategy oms_type both align; fail-fast runner-level too.

---

## 4) Risk Controls — Hot Path Verification
**Status:** 🟡 Needs Improvement
**Risk Level:** Critical

**Findings**
1. **Drawdown protection** is called at top of `on_bar()` before trading pipeline. Good.
2. **Circuit breaker** is invoked from `on_order_rejected()` and `on_order_denied()`. Good.
3. **Position size validation** is called in `_execute_add()` before submit. Good.
4. **Critical issue:** `_check_drawdown_limit()` uses `account.balance_total(USDT)` only; this may not capture full unrealized risk path semantics expected by spec (“unrealized drawdown from peak”).
5. Config access pattern is largely nested (`cfg.risk_management.*`, `cfg.position.max_position_pct`), avoiding root `getattr` bug pattern in risk gates.
6. Risk checks can be bypassed by non-grid emergency/manual paths (e.g., flatten/TP/SL submission path not size-validated by design).

**Recommendation**
- Compute drawdown from equity (realized + unrealized) if available via portfolio/account APIs; otherwise explicitly document current behavior as balance-based.
- Add tests that fail if someone reintroduces root-level `getattr` config access.

---

## 5) NautilusTrader Strategy Lifecycle Correctness
**Status:** 🟢 Good
**Risk Level:** Medium

**Findings**
- Lifecycle hooks are implemented (`__init__`, `on_start`, `on_bar`, `on_data`, fill/accept/cancel/reject/denied, `on_stop`).
- `on_bar` order is effectively: drawdown → warm checks/circuit → recenter/build → policy → funding → throttle → precision/diff → execute.
- Mark-price subscription for funding updates exists with safe fallback in backtest.

**Recommendation**
- Add an integration test asserting `on_bar` stage ordering to prevent regression.

---

## 6) Exchange API & Connectivity Resilience
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Uses Nautilus Binance clients in runner (`BinanceLiveDataClientFactory`, `BinanceLiveExecClientFactory`).
- Testnet monkey patch filters non-ASCII symbols; useful but monkey patches are fragile and can mask upstream changes.
- Warmup is non-blocking by design; startup continues even if warmup fails.
- Limited explicit handling for 429/backoff and websocket staleness in strategy layer; mostly delegated to adapter.

**Recommendation**
- Add explicit stale-data watchdog (no bars/mark updates for N seconds => pause trading).
- Wrap warmup in stricter health mode for live (configurable: block vs non-blocking).

---

## 7) Operational Controls & Kill Switch
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- OperationsManager correctly boots Prometheus, API, metrics poller, and KillSwitch when enabled.
- KillSwitch can flatten via strategy callback and prevents duplicate flatten via lock.
- Alert manager errors are contained via async gather with exception logging.
- KillSwitch in manager starts with `alert_manager=None`; alerts are not actually wired by default despite config support.

**Recommendation**
- Instantiate `AlertManager` from operations config and pass into `KillSwitch`.
- Add explicit “kill-switch trigger context” structured logs with metric snapshot.

---

## 8) FastAPI REST API Security
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- Write endpoints are protected by `_validate_write_auth`; when no API key and `require_auth=True`, writes are blocked (403).
- Read endpoints can be open when no API key configured (intended monitoring behavior).
- API has per-IP rate limiting middleware (GET/POST buckets).
- Default bind is localhost in server start path from OperationsManager.
- If operator explicitly binds externally or disables auth (`require_auth=False`), flatten/throttle become network-risky.

**Recommendation**
- Force auth in live mode regardless of operator flag; deny start if no API key.
- Add allowed CIDR list / mTLS option for production hardening.

---

## 9) Security Audit
**Status:** 🟡 Needs Improvement
**Risk Level:** High

**Findings**
- No hardcoded trading API keys found in code paths reviewed.
- YAML env-var substitution supports `${VAR}` and defaults.
- Dependency versions are mostly minimum-bound only (`nautilus-trader` unpinned), raising supply-chain drift risk.
- Docker runs non-root user and avoids embedding secrets.
- SQLite writes use parameterized SQL for values; one query uses f-string for a static boolean filter fragment (not user-injected in current code).

**Recommendation**
- Add lockfile enforcement in CI and vulnerability scan (`pip-audit`/`safety`).
- Use restrictive file permissions for runtime config mounts and secrets.

---

## 10) Concurrency & Race Conditions
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Strategy assumes Nautilus actor-style event sequencing; internal locks added for shared structures.
- API callbacks run via threadpool and can call into strategy while strategy processes events; this cross-thread access is not uniformly synchronized.
- Some fields have locks (`_fills_lock`, `_grid_orders_lock`, `_ladder_lock`), but not all mutable state transitions are guarded.

**Recommendation**
- Route API mutations onto strategy/event thread mailbox instead of direct method execution.

---

## 11) Error Handling & Resilience
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Many `except Exception` blocks log and continue; some are appropriate, others may mask logic defects.
- Warmup failure is soft-fail; may be acceptable for paper/backtest but risky for live if detector remains cold too long.
- Crash recovery partly handled via order cache hydration and position reconciliation; no persistent durable strategy state store.

**Recommendation**
- Classify exceptions: recoverable vs fatal. Promote unexpected invariants to critical fail/pause.

---

## 12) Code Quality & Cleanliness
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- `strategy.py` is very large/complex and mixes orchestration with many concerns.
- Some magic constants remain (cooldowns, tolerances, queue limits) though many are now configurable.
- Tests/docs mention stubs and skipped tests indicating unfinished integration paths.

**Recommendation**
- Decompose `strategy.py` into smaller services and add architectural linting.

---

## 13) Configuration Validation
**Status:** 🟢 Good
**Risk Level:** Medium

**Findings**
- Pydantic v2 models enforce robust bounds and relationships across strategy/operations/venue configs.
- Loader fails fast with readable field-path validation errors.
- Environment variable substitution in YAML is implemented and validated.
- Environment separation (testnet vs production) is possible but still operator-dependent by selected config files.

**Recommendation**
- Add explicit live-mode guard: reject `testnet: true` + live runner combination unless override flag is set.

---

## 14) Testing Gaps
**Status:** 🔴 Critical Issue
**Risk Level:** Critical

**Findings**
- In this environment, test execution failed at collection due missing deps (`pydantic`, `numpy`, `fastapi`, `nautilus_trader`, etc.), preventing verification of claimed pass/skip counts.
- Static skip markers found in parity, ops integration, and data pipeline tests; parity tests are explicitly skipped pending refactor.
- I could not verify the “645 tests / 37 skipped” claim from current environment state.

**Recommendation**
- Run CI in reproducible env (uv lock sync) and publish authoritative test matrix (pass/fail/skip with reasons).
- Add dedicated tests for: risk gate non-bypass, `getattr` bug regression, hedge-mode position_id invariants, crash/restart hydration.

---

## 15) Data Pipeline Integrity
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Schema validation and conversion paths are explicit and typed.
- Normalizer handles timestamp normalization, dedup, and sanity filters.
- Gap detection exists conceptually but no strict “halt on gap” enforcement in pipeline outputs.
- `create_instrument()` uses fixed precision/fees defaults that may diverge from real instrument metadata and distort backtest fidelity.

**Recommendation**
- Pull instrument metadata dynamically from source venue snapshots for replay realism.
- Add optional strict mode: fail pipeline on temporal gaps > threshold.

---

## 16) Performance
**Status:** 🟡 Needs Improvement
**Risk Level:** Medium

**Findings**
- Hot path does full diff each bar and significant logging; likely acceptable at 1m bars, weaker for higher frequency.
- In-memory caches have pruning in some places but not all histories are bounded by policy docs.
- Optimization DB uses SQLite with immediate transactions; fine for moderate parallelism, potential contention at high worker counts.

**Recommendation**
- Add performance budget tests (bar-processing latency percentiles).
- Reduce info-level logging in live/optimization hot path.

---

# Executive Summary

## Top 5 Critical Issues (fix before live)
1. Drawdown check semantics likely balance-based rather than true equity/unrealized drawdown.
2. TP/SL reduce-only workaround (`reduce_only=False`) depends on perfect position_id routing and carries execution safety risk.
3. API/ops cross-thread mutation model can race strategy state.
4. Test baseline is not reproducible in current env; critical assertions are unverified.
5. KillSwitch alerts are not wired in default manager path (`alert_manager=None`).

## Top 5 Robustness Improvements
1. Add stale-market-data watchdog and auto-pause.
2. Make warmup blocking policy configurable by mode (live stricter).
3. Introduce mailbox-style API command execution on strategy thread.
4. Add structured incident logs for kill-switch triggers.
5. Expand restart recovery tests including order cache hydration and TP/SL reattachment.

## Top 5 Code Quality Improvements
1. Break down `strategy.py` into orchestrator + services.
2. Replace remaining magic constants with config fields.
3. Add architecture contracts/interfaces between ops/api and strategy.
4. Tighten exception taxonomy (recoverable vs fatal).
5. Add regression tests for known config-access bug patterns.

## Overall Health Score
**7.1 / 10** — solid core design and many safeguards, but live-readiness risk remains around risk semantics, concurrency boundaries, and reproducible verification.

## Prioritized Action Plan (risk × effort)
1. **High risk / medium effort:** Rework drawdown to equity-based metric + tests.
2. **High risk / medium effort:** Thread-safe command mailbox between API/ops and strategy.
3. **High risk / low effort:** Wire AlertManager into KillSwitch startup path.
4. **High risk / low effort:** Enforce live-mode auth/API-key hard requirement.
5. **Medium risk / medium effort:** Tune diff tolerances via config per symbol/regime.
6. **Medium risk / medium effort:** Decompose strategy monolith into testable components.
7. **Medium risk / low effort:** Add CI vulnerability + dependency pin checks.
