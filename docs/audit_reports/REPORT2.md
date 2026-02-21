# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope: repository-wide static audit of architecture, strategy hot path, ops stack, UI, data pipeline, optimization stack, and tests.
Method: direct file review + lightweight static checks (`pytest -q`, `rg` skip scan, import graph scan).

---

## 1) Architecture & Layered Structure Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Layering is mostly respected (strategy orchestrator depends on reusable `strategy/*`, domain, exchange precision), but there are notable leaks: `BaseRunner` applies Binance testnet monkey patch globally at import time, regardless of mode/environment (`naut_hedgegrid/runners/base_runner.py:14-18`). `OperationsManager` reaches into strategy private fields (`_last_long_ladder`, `_ladder_lock`, `_get_live_grid_orders`) rather than stable interface methods (`naut_hedgegrid/ops/manager.py:229-259`). `PlacementPolicy` ignores `cfg.policy.strategy` and always uses the same throttling behavior, so “core-and-scalp” vs “throttled-counter” are not materially separated (`naut_hedgegrid/strategy/policy.py:17-56`, `naut_hedgegrid/config/strategy.py:176-191`). |
| **Risk Level** | Medium |
| **Recommendation** | Gate testnet patch behind explicit runtime condition (`venue_cfg.api.testnet`), provide public strategy API methods for ladder/order snapshots, and implement policy strategy branching (or remove unused enum option). |

## 2) Trading Logic & Grid Engine Correctness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `GridEngine.build_ladders()` correctly builds LONG below mid and SHORT above mid, and qty scaling is geometric (`naut_hedgegrid/strategy/grid.py:45-172`). `recenter_needed()` is clean (`grid.py:218-237`). However, `RegimeDetector` computes ATR but ATR is not used in classification decisions (`naut_hedgegrid/strategy/detector.py:360-406`), reducing intended volatility adaptivity. `PlacementPolicy` does not implement distinct behavior for `core-and-scalp` strategy option (`policy.py:88-130`). `OrderDiff` tolerance defaults (1 bps, 1% qty) are reasonable but may still churn near tick boundaries in high-vol instruments (`order_sync.py:58-121`). |
| **Risk Level** | Medium |
| **Recommendation** | Use ATR in regime thresholds or remove it from decision path to avoid false confidence. Add explicit policy-mode switch and tests validating both modes. Add churn metrics (replace/cancel ratio) to tune tolerances per symbol. |

## 3) Hedge Mode Position Management

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Position ID suffixing is generally consistent (`{instrument}-LONG/SHORT`) in submit paths and reconciliation (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:608,1122,1774,2409-2427`). OmsType defaults to HEDGING in strategy config (`naut_hedgegrid/strategies/hedge_grid_v1/config.py:34-35`). But runner derives OMS from venue config and can set NETTING (`naut_hedgegrid/runners/base_runner.py:191-207`), so hedge-mode is not strictly enforced. |
| **Risk Level** | High |
| **Recommendation** | Fail-fast at startup if `oms_type != HEDGING` for this strategy. Add guard in `on_start()` and runner validation. |

## 4) Risk Controls — Hot Path Verification

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Drawdown check is present but not literally at top of `on_bar()`; regime updates happen first and warmup early-return can bypass drawdown logic (`strategy.py:742-763`). Circuit breaker hooks are wired from both reject and deny handlers (`strategy.py:1573-1575`, `1614-1615`). Order-size validation is called in `_execute_add` before grid submits (`strategy.py:1767-1772`) and uses nested config access (good: `risk_management`, `position.max_position_pct`) (`strategy.py:2480-2501`). But TP/SL and flatten market orders bypass `_validate_order_size` by design (`strategy.py:1203-1204`, `2424-2427`). |
| **Risk Level** | High |
| **Recommendation** | Move `_check_drawdown_limit()` immediately after initialization checks in `on_bar()`. Add separate bypass-limited validation rules for emergency/exit orders and log explicit policy when bypassing size checks. |

## 5) NautilusTrader Strategy Lifecycle Correctness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Lifecycle shape is mostly correct (`__init__`, `on_start`, `on_bar`, `on_data`, fill/accept/cancel/reject/deny, `on_stop`). Documented bar flow largely matches implementation (`strategy.py:692-890`). `on_start()` contains broad `except Exception` around config load and subscription paths, which can hide startup faults and continue in partially degraded state (`strategy.py:201-209`, `307-312`). |
| **Risk Level** | Medium |
| **Recommendation** | Narrow exception scopes and classify fatal vs non-fatal startup failures. Emit startup health summary with required/optional components. |

## 6) Exchange API & Connectivity Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Warmup supports pagination and delay (`binance_warmer.py:118-176`), but is synchronous in `on_start()` and can delay startup; failure is non-blocking as documented (`strategy.py:331-457`). Testnet patch is global and unconditional in runner import path (`base_runner.py:14-18`) and can affect production codepaths. Explicit 429/backoff handling is not implemented in warmer (`binance_warmer.py:142-153`). |
| **Risk Level** | Medium |
| **Recommendation** | Apply patch only when testnet enabled; add retry/backoff for HTTP status 429/5xx and bounded warmup timeout. |

## 7) Operational Controls & Kill Switch

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Ops wiring exists and starts Prometheus/API/KillSwitch (`ops/manager.py:68-118`). KillSwitch trigger and flatten flow is robustly structured (`ops/kill_switch.py:167-241`, `439-486`). However, manager constructs `KillSwitchConfig()` defaults directly, ignoring environment/runtime config loading (`ops/manager.py:100-107`). Alert failures are handled with `gather(..., return_exceptions=True)` (good) (`ops/alerts.py:166-176`). |
| **Risk Level** | Medium |
| **Recommendation** | Inject ops config explicitly into `OperationsManager`; avoid hidden defaults for production risk thresholds. |

## 8) FastAPI REST API Security

| Field | Details |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | Authentication is optional; if `api_key` unset, all control endpoints are unauthenticated, including flatten and throttle (`ui/api.py:249-266`, `361-412`). API server binds `0.0.0.0` by default and CORS is wildcard with credentials enabled (`ui/api.py:210-216`, `457-483`). No endpoint-level rate limiting exists. |
| **Risk Level** | Critical |
| **Recommendation** | In live mode, require API key (or disable control API). Default bind to `127.0.0.1`; make host explicit for remote exposure. Add rate limiting (e.g., slowapi/token bucket) for mutating endpoints. |

## 9) Security Audit

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | No hardcoded exchange keys found; YAML env substitution supports `${VAR}` and defaults (`utils/yamlio.py:13-57`). Dependency constraints are broad/open (e.g., `nautilus-trader`, many unpinned core libs), making supply-chain/CVE control weaker (`pyproject.toml:8-28`). Docker uses non-root user (good), but Dockerfile copies `src/` while repo package is `naut_hedgegrid/`, likely producing broken runtime image (`docker/Dockerfile:67-69`). |
| **Risk Level** | High |
| **Recommendation** | Pin critical runtime dependencies via lockfile enforcement in CI and add `pip-audit`/`uv audit`. Fix Docker copy paths and add image smoke test. |

## 10) Concurrency & Race Conditions

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Strategy includes multiple locks for shared state (`_order_id_lock`, `_fills_lock`, `_ladder_lock`, `_grid_orders_lock`) indicating API/thread interaction awareness (`strategy.py:118-160`). API callbacks execute in thread pool (`ui/api.py:197-241`), and ops callback mutates strategy state (`ops/manager.py:218-228`), so cross-thread writes exist (`_throttle`, flatten actions). Some writes are atomic primitives, but complex operations rely on ad-hoc locking. |
| **Risk Level** | Medium |
| **Recommendation** | Introduce single command queue into strategy actor thread for mutating API actions (`flatten`, `set_throttle`) instead of direct cross-thread method calls. |

## 11) Error Handling & Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Extensive `except Exception` usage in strategy and ops can suppress root causes (`strategy.py:201-209`, `1219-1225`, `1568-1571`; `ops/manager.py:81-84,113-115`). Some are intentional for liveness, but currently mixed with critical paths. Crash recovery for open-order cache exists (`_hydrate_grid_orders_cache`) (`strategy.py:546-585`) but broader state is largely in-memory (no persisted strategy state). |
| **Risk Level** | Medium |
| **Recommendation** | Distinguish recoverable/non-recoverable exceptions; emit structured error counters and fail-fast on configuration/state corruption. Persist minimal critical state snapshot (grid center, fill tracking epoch). |

## 12) Code Quality & Cleanliness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `strategy.py` is very large and multi-responsibility (>2.6k lines), increasing cognitive and change risk. There is dead/stub behavior in API start/stop semantics (docs imply operations, callback currently always returns generic success/error dictionary paths in manager). Multiple magic constants in strategy risk and timing logic (e.g., 60s ns windows, 0.05% SL adjustment) (`strategy.py:2540`, `1959`, `1972`). |
| **Risk Level** | Medium |
| **Recommendation** | Split strategy into lifecycle/risk/exits/order-exec modules. Move constants into config with safe defaults and documentation. |

## 13) Configuration Validation

| Field | Details |
|---|---|
| **Status** | 🟢 Good |
| **Findings** | Pydantic v2 models enforce strong bounds and relationships for grid, regime, exits, and risk (`config/strategy.py:16-257`). Nested config access pattern in risk checks is correct (avoids root `getattr` anti-pattern) (`strategy.py:2480-2501`, `2522-2556`, `2568-2603`). |
| **Risk Level** | Low |
| **Recommendation** | Add startup validation asserting environment/mode compatibility (testnet/live) and mandatory auth when ops API enabled in live mode. |

## 14) Testing Gaps

| Field | Details |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | In this environment, tests fail during collection due to missing dependencies (`pytest -q` yielded 20 collection errors). Static skip scan found explicit skip markers in data pipeline, ops integration, and parity tests (`tests/data/test_pipeline.py:47,280,327`; `tests/test_ops_integration.py:51,180,219,251`; `tests/test_parity.py:573,639`; plus runtime skip in `tests/strategy/test_strategy_smoke.py:154`). Claimed “645 tests / 37 skipped” could not be verified here. |
| **Risk Level** | Critical |
| **Recommendation** | Enforce CI matrix with full dependency install; publish skip-report artifact each run. Add dedicated tests for: OMS netting guardrail, API auth required in live mode, and drawdown-gate call order. |

## 15) Data Pipeline Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Schemas and normalization are generally solid with UTC normalization and schema checks (`data/schemas.py`, `data/pipelines/normalizer.py`). However, validation samples only first 10 rows in `validate_dataframe_schema`, allowing later corrupt rows to slip through (`data/schemas.py:292-298`). |
| **Risk Level** | Medium |
| **Recommendation** | Validate full dataframe in strict mode (or probabilistic + tail/head windows with error budget), especially for production ingest and backtest artifacts. |

## 16) Performance

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Hot path is efficient conceptually (diff-based reconciliation, cache), but heavy logging in `on_bar` at info/debug level can materially impact live throughput (`strategy.py:818-825`, plus repeated status logs). Some in-memory sets/maps have bounded cleanup (good), but caches still require monitoring under long uptime (`strategy.py:2021-2035`, `251-? rejection maps`). |
| **Risk Level** | Medium |
| **Recommendation** | Reduce per-bar logging in live mode, add periodic metrics for cache sizes/churn, and benchmark on real bar throughput. |

---

## Executive Summary

### 1) Top 5 Critical Issues (fix before live)
1. **Control API can be unauthenticated while exposed on `0.0.0.0` with permissive CORS** (`ui/api.py`).
2. **OMS hedge-mode not hard-enforced**; runner can pass NETTING (`runners/base_runner.py`).
3. **TP/SL and flatten close orders are non-reduce-only by design** (`strategy.py:1878-1890`, `1982-1991`, `2418-2425`) — requires very careful exchange-side guarantees.
4. **Test execution currently not reproducible in this environment** (dependency gaps; no verified pass/fail baseline).
5. **Docker runtime packaging path mismatch (`src/` vs actual package path)** can break deployment image.

### 2) Top 5 Robustness Improvements
1. Add API auth hard-requirement + local bind default in live mode.
2. Add startup hard checks for OMS_HEDGING and venue-mode consistency.
3. Introduce command queue for API-triggered strategy mutations.
4. Add 429/backoff handling and warmup timeout controls.
5. Persist minimal critical runtime state for restart continuity.

### 3) Top 5 Code Quality Improvements
1. Decompose `strategy.py` into smaller modules.
2. Implement true policy-mode branching (`core-and-scalp` vs `throttled-counter`).
3. Replace magic numbers with config fields.
4. Tighten exception granularity on startup/hot path.
5. Add full-data validation mode in data schemas pipeline.

### 4) Overall Health Score
**6.3 / 10** — Strong core structure and domain modeling, but live-ops security posture and environment/test reproducibility are not production-safe yet.

### 5) Prioritized Action Plan (risk × effort)
1. **[High risk / Low effort]** Require API key + localhost bind by default; disable unauthenticated control endpoints in live.
2. **[High risk / Low effort]** Enforce `OmsType.HEDGING` fail-fast at startup.
3. **[High risk / Medium effort]** Validate/guard non-reduce-only exit behavior with explicit exchange-side checks and invariant tests.
4. **[Medium risk / Low effort]** Fix Docker copy paths and add container smoke test.
5. **[Medium risk / Medium effort]** Implement policy strategy branching + tests.
6. **[Medium risk / Medium effort]** Add CI dependency lock/audit and publish skip reasons.
7. **[Medium risk / Medium effort]** Refactor strategy monolith into cohesive subcomponents.
