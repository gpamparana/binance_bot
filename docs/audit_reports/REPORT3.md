# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope: `naut_hedgegrid/`, `tests/`, `configs/`, `docker/`, and project metadata. Audit is static (code + config inspection) plus limited runtime checks where dependencies permitted.

## 1) Architecture & Layered Structure Integrity

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Layering is mostly clean in package topology, but `strategy/*` imports `config.strategy` directly (`strategy/grid.py:5`, `strategy/policy.py:3`), coupling reusable components to full config models instead of minimal interfaces. `strategies/hedge_grid_v1/strategy.py` acts as orchestrator and delegates to `GridEngine`, `PlacementPolicy`, `FundingGuard`, `OrderDiff`, and `RegimeDetector` in the intended order (`strategy.py:811-887`). Domain types (`Side`, `Rung`, `Ladder`, `OrderIntent`) are used consistently in grid/policy/sync paths (`grid.py:6`, `policy.py:4`, `order_sync.py:13-21`). |
| Risk Level | Medium |
| Recommendation | Introduce protocol-style lightweight config DTOs for `GridEngine`/`PlacementPolicy` and keep `HedgeGridConfig` parsing at orchestration boundaries only. Add import-layer lint checks (e.g., import-linter contract) to fail CI on forbidden layer edges. |

## 2) Trading Logic & Grid Engine Correctness

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Grid side geometry is correct: LONG prices below mid (`grid.py:83-87`), SHORT prices above mid (`grid.py:141-145`), geometric qty scaling (`grid.py:89-90`, `147-148`), recenter condition in bps (`grid.py:238-244`). Policy implementation ignores `policy.strategy` and always applies throttled-counter behavior (`policy.py:41-59`, `101-127`), so `core-and-scalp` is not differentiated. RegimeDetector uses EMA spread + ADX threshold + hysteresis (`detector.py:394-411`, `397-405`), but warmup gating allows ADX/ATR to lag classification because `_update_regime` only requires EMAs (`detector.py:380-383`) while `is_warm` requires all indicators (`421`). FundingGuard projects cost by window fraction of 8h periods (`funding_guard.py:118-123`) and adjusts only paying side (`96-103`, `141-143`)—reasonable but simplistic. |
| Risk Level | High |
| Recommendation | Implement explicit branch on `cfg.policy.strategy` (e.g., maintain minimum rungs on both sides for `core-and-scalp`). Make regime classification require full warmup (or make warmup policy explicit in docs/tests). Add regression tests verifying strategy mode semantics. |

## 3) Hedge Mode Position Management

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Hedge IDs use `{instrument_id}-{LONG|SHORT}` consistently (`strategy.py:661`, `1007`, `1012`, `1774`, `2427`). `submit_order(..., position_id=...)` is used for grid and flatten paths (`1777`, `2427`) and TP/SL paths (`1203-1204`). `_hydrate_grid_orders_cache()` prevents duplicate initial adds by reconstructing open grid orders (`457-506`). However, OMS can still be NETTING if venue config sets `hedge_mode=false` (`base_runner.py:203`, `461`), despite strategy config defaulting to HEDGING (`hedge_grid_v1/config.py:33`). |
| Risk Level | High |
| Recommendation | Fail fast at startup if `hedge_mode` is false for this strategy. Add runtime assertion in `on_start` to verify `self.config.oms_type == OmsType.HEDGING`. |

## 4) Risk Controls — Hot Path Verification

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Drawdown gate is invoked in `on_bar` before ladder build/diff/submit (`strategy.py:762-764`), and `_pause_trading` short-circuits processing (`710-713`). Circuit breaker is triggered from both rejected and denied handlers (`1575`, `1611`) with per-minute window and cooldown (`2536-2557`). Position validation is called from `_execute_add` before submit (`1769`, `1777`) and uses nested config path (`2500`), avoiding root `getattr` bug pattern. Main gap: `_validate_order_size` checks single-order notional against free balance (`2491-2503`), not “current position + proposed order” as requested. TP/SL attachment submits directly without `_validate_order_size` (`1203-1204`). |
| Risk Level | High |
| Recommendation | Extend `_validate_order_size` to aggregate existing side exposure + pending order notional and include leverage/margin constraints. Decide and codify whether reduce-side exits bypass position gates. |

## 5) NautilusTrader Strategy Lifecycle Correctness

| Field | Details |
|---|---|
| Status | 🟢 Good |
| Findings | `__init__` initializes state and no external side effects (`strategy.py:74-188`). `on_start` loads config, instrument, components, subscribes bars + mark price data, hydrates cache, warmup (`198-332`). `on_bar` executes pipeline in intended order: risk → regime warmup gate → recenter/build → policy → funding → throttle → diff → execute (`762-887`). `on_data` forwards funding updates (`919-932`). Fill/accept/cancel/reject/deny handlers exist and update tracking (`939+`, `1360+`, `1449+`, `1577+`). `on_stop` cancels open strategy orders (`434-454`). |
| Risk Level | Low |
| Recommendation | Add lifecycle integration test asserting exact processing order via call tracing/mocks. |

## 6) Exchange API & Connectivity Resilience

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Integration goes through Nautilus Binance adapter and data subscriptions (`strategy.py:298-307`, `919-932`). Testnet patch is global monkey patch with no environment guard in function itself (`binance_testnet_patch.py:10-40`), so accidental invocation in prod could alter behavior globally. Warmup uses plain HTTPX with basic rate delay (`binance_warmer.py:39`, `176-179`) and raises exceptions on HTTP errors (`252-255`), while strategy catches and continues (non-blocking) in `_perform_warmup`; safe operationally but can start “cold.” No explicit 429 backoff policy in warmer. |
| Risk Level | Medium |
| Recommendation | Gate patch application by explicit `if testnet` check at call site + idempotence marker. Add retry/backoff for 429/5xx in warmer and explicit stale-data detection fallback in live mode. |

## 7) Operational Controls & Kill Switch

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | `OperationsManager` wires Prometheus, API, metrics poller, and KillSwitch (`manager.py:69-116`). KillSwitch monitors drawdown/funding/margin/loss circuits and calls strategy flatten (`kill_switch.py:282-302`, `219-244`). If flatten fails, it returns error dict and logs (`246-255`) but no guaranteed escalation path. Manager currently instantiates `KillSwitchConfig()` defaults rather than loading runtime ops config (`manager.py:100-107`). |
| Risk Level | Medium |
| Recommendation | Inject real operations config from CLI/runner and enforce fail-closed behavior when flatten repeatedly fails (e.g., repeated alerts + trading pause hook). |

## 8) FastAPI REST API Security

| Field | Details |
|---|---|
| Status | 🔴 Critical Issue |
| Findings | API key auth is optional by design; if `api_key` unset, privileged endpoints are unauthenticated (`api.py:258-260`, used by `/flatten`, `/set-throttle` at `361-411`). Server defaults bind to `0.0.0.0` (`api.py:457`, `manager.py:82`), and CORS is fully permissive `allow_origins=["*"]` (`api.py:210-216`). No request rate limiting. |
| Risk Level | Critical |
| Recommendation | In live mode, require API key and bind default to `127.0.0.1`. Add middleware rate limiting and origin allowlist. Consider disabling mutating endpoints unless explicit `--enable-ops-write`. |

## 9) Security Audit

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | No obvious hardcoded exchange keys; configs use `${ENV_VAR}` substitution (`configs/venues/binance_futures*.yaml`, `yamlio.py:37-57`). Venue config files are world-readable (0644) (`stat` output), increasing local secret exposure risk if resolved values are persisted. Dockerfile runs as non-root (`Dockerfile:56-77`), good baseline. Dependencies are loosely pinned (`pyproject.toml:10-30`), so CVE surface is not deterministic without lock/scan. `results_db.py` uses parameterized SQL placeholders (safe against injection) (`results_db.py` inserts/selects with `?`). |
| Risk Level | Medium |
| Recommendation | Tighten permissions for secret-bearing configs (`chmod 600`), enforce lockfile + vulnerability scanning in CI, and avoid logging credential-bearing URLs/tokens in exceptions. |

## 10) Concurrency & Race Conditions

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Strategy uses multiple locks (`_order_id_lock`, `_fills_lock`, `_ladder_lock`, `_grid_orders_lock`) indicating cross-thread access expectations (`strategy.py:120-170`). API callback runs through threadpool (`api.py:198-241`) and manager accesses strategy internals directly (`manager.py:176-177`, `240-255`), so shared mutable state (`_throttle`, ladders, caches) can race against event loop handlers. Some accesses are locked, but not all (e.g., direct `_grid_center` read in callback). |
| Risk Level | High |
| Recommendation | Introduce single-thread command queue into strategy actor (message passing), and make API callbacks enqueue commands instead of mutating strategy internals directly. |

## 11) Error Handling & Resilience

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | There are broad exception handlers in hot paths (`strategy.py:1228-1233`, `2511-2513`, `2613-2615`; `kill_switch.py:276-277`)—some intentionally fail-safe, but they reduce observability. Several places swallow exceptions without telemetry (`on_data` ImportError pass: `933-934`; realized PnL block `1015-1016`). Crash recovery is partially addressed via order hydration and position reconciliation (`457-506`, `511-571`), but critical state remains mostly in-memory. |
| Risk Level | Medium |
| Recommendation | Replace silent `pass` with structured debug logging + counters. Persist minimal recovery state (last center, tracked TP/SL map) for restart robustness. |

## 12) Code Quality & Cleanliness

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | `strategy.py` is very large/complex (2.6k+ LOC with many responsibilities), increasing cognitive load and defect risk. Some dead/stub behavior exists in ops callback (`start`/`stop` endpoint pathways depend on callback implementation that may not be wired in strategy). Magic numbers appear in risk/timing logic (e.g., 60s nanos, 0.05% SL adjust) (`strategy.py:2540`, `1959`, `1972`). Type hints are generally good; localized `type: ignore` usages in sync/retry may hide mismatches (`order_sync.py:219`, `strategy.py:1535`). |
| Risk Level | Medium |
| Recommendation | Split strategy into focused services (risk engine, order lifecycle, exit manager). Move magic constants into config. Reduce `type: ignore` via stronger typed helper methods. |

## 13) Configuration Validation

| Field | Details |
|---|---|
| Status | 🟢 Good |
| Findings | Pydantic v2 models include strong bounds and validators across grid/risk/regime/policy (`config/strategy.py:20-248`) and ops config (`config/operations.py:40-113`). Invalid examples like negative grid levels are blocked by schema constraints (`grid_levels_* ge=1`). However, environment separation is not strict enough: runner can select NETTING depending on venue config (`base_runner.py:203`). |
| Risk Level | Medium |
| Recommendation | Add startup invariant checks: strategy requires hedge mode, and production config must reject testnet flags and vice versa unless explicit override. |

## 14) Testing Gaps

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Current environment cannot execute full suite due missing deps (`pytest` collection failed with missing `pydantic`, `numpy`, `fastapi`, `nautilus_trader`, etc.). Explicit skipped tests in-tree are fewer than claimed 37: found 9 skip markers via search (`tests/data/test_pipeline.py`, `tests/test_ops_integration.py`, `tests/test_parity.py`, one runtime skip in `test_strategy_smoke.py`). Parity tests are currently skipped (`tests/test_parity.py:573`, `639`). |
| Risk Level | High |
| Recommendation | Reproduce CI environment and publish authoritative skip report from `pytest -rs`. Add targeted tests for config-access bug pattern, hedge side TP/SL isolation, crash-recovery hydration, and drawdown/circuit breaker bypass attempts. |

## 15) Data Pipeline Integrity

| Field | Details |
|---|---|
| Status | 🟢 Good |
| Findings | Schemas validate timestamp/timezone and key constraints with conversion helpers (`data/schemas.py:45-63`, `86-129`, conversion functions). Normalizer handles dedupe, sorting, and timestamp normalization with unit heuristics (`normalizer.py:83-90`, `152-159`, `228-295`). Potential corner case: timestamp unit detection uses first sample magnitude (`272-285`), which can misclassify mixed-format series. |
| Risk Level | Medium |
| Recommendation | Enforce homogeneous timestamp types pre-normalization and add anomaly checks for non-monotonic large jumps. |

## 16) Performance

| Field | Details |
|---|---|
| Status | 🟡 Needs Improvement |
| Findings | Hot path contains heavy logging at info level per bar (`strategy.py:818-883`) and multiple cache traversals/order parsing; could impact live latency. Some bounded structures exist (error deque maxlen=100, parse cache capped at 1000) (`strategy.py:141`, `2031-2037`)—good. API metrics polling every 5s is lightweight (`manager.py:167-171`). |
| Risk Level | Medium |
| Recommendation | Reduce per-bar info logging in live mode, precompute/ cache parsed IDs where possible, and benchmark diff+execute latency under peak order counts. |

---

## Executive Summary

### 1. Top 5 Critical Issues (fix before live)
1. **Unauthenticated remote control risk** when API key unset + `0.0.0.0` bind + permissive CORS (`ui/api.py`, `ops/manager.py`).
2. **Hedge-mode safety not guaranteed** because runner can switch to NETTING via venue config (`runners/base_runner.py:203`).
3. **Position-size gate incomplete** (does not include current side exposure; only per-order notional) (`strategy.py:2491-2503`).
4. **Cross-thread strategy mutation risk** via API callback/threadpool + direct internal access (`ui/api.py:198-241`, `ops/manager.py:240-255`).
5. **Policy mode mismatch**: `core-and-scalp` config not behaviorally distinct (`strategy/policy.py`).

### 2. Top 5 Robustness Improvements
1. Add strict startup invariants (hedge mode, live/testnet separation).
2. Introduce command-queue pattern for all API-originated strategy mutations.
3. Add retry/backoff and explicit 429 handling in warmup and data fetch components.
4. Persist critical recovery state across restarts (grid center, exit map).
5. Add structured error counters for swallowed exceptions.

### 3. Top 5 Code Quality Improvements
1. Decompose `strategies/hedge_grid_v1/strategy.py` into smaller services.
2. Replace magic constants with config fields.
3. Enforce architecture imports via lint contracts.
4. Improve type safety to remove broad `type: ignore` usage.
5. Align docs/comments with real behavior (policy modes, start/stop endpoints, warmup guarantees).

### 4. Overall Health Score
**6.7 / 10** — Strong foundational design (typed configs, modular engines, risk hooks present) but live-security posture and hedge/risk invariants need hardening before production deployment.

### 5. Prioritized Action Plan (risk × effort)
1. **P0 (Immediate):** Lock down API (required auth in live, localhost bind, CORS allowlist, rate limiting).
2. **P0:** Enforce `OmsType.HEDGING` and reject NETTING at runner + strategy startup.
3. **P1:** Upgrade `_validate_order_size` to side-aware aggregate exposure checks.
4. **P1:** Refactor API→strategy interaction into serialized command queue.
5. **P2:** Implement true `core-and-scalp` behavior and add mode-specific tests.
6. **P2:** Add warmup/data fetch retry/backoff and stale data detection.
7. **P3:** Split monolithic strategy class and reduce logging overhead.
