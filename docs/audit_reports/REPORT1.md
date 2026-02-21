# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope: repository-wide static audit focused on hedge-mode grid trading, risk controls, operations, API security, and production readiness.

## 1) Architecture & Layered Structure Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Layering is mostly clean (strategy orchestrator imports reusable components and domain types), but boundary leakage exists: `ops/manager.py` directly accesses strategy internals (`_ladder_lock`, `_last_long_ladder`, `_last_short_ladder`, `_grid_center`) instead of an explicit interface, tightly coupling Ops↔Strategy (`naut_hedgegrid/ops/manager.py:230-249`). `BaseRunner` hardwires `OmsType` from venue hedge flag, permitting NETTING fallback (`naut_hedgegrid/runners/base_runner.py:203,461`). |
| **Risk Level** | Medium |
| **Recommendation** | Add a formal strategy ops interface (read-only DTO getters + command methods), and enforce HEDGING-only for this strategy at runner validation time. |

## 2) Trading Logic & Grid Engine Correctness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Grid ladder directions and geometric sizing are correct (`grid.py`: long below mid, short above mid, qty scale geometric) (`naut_hedgegrid/strategy/grid.py:47-169`). `PlacementPolicy` ignores `policy.strategy` and always applies throttled-counter behavior for trending regimes; `core-and-scalp` has no distinct implementation (`naut_hedgegrid/strategy/policy.py:34-57,90-128`). `RegimeDetector` computes ATR but does not use it in classification (only EMA spread + ADX + hysteresis), so volatility-aware branch is effectively absent (`naut_hedgegrid/strategy/detector.py:360-408`). |
| **Risk Level** | Medium |
| **Recommendation** | Implement explicit strategy switch in `PlacementPolicy.shape_ladders()` and add ATR-informed logic or remove ATR from config to avoid false confidence. |

## 3) Hedge Mode Position Management

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Position ID suffixing is consistently used in most order submissions (`{instrument}-LONG/SHORT`) including TP/SL submission paths (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:608-686,1121-1204,1773-1777`). However, OMS can still become NETTING from runner config as noted above. |
| **Risk Level** | High |
| **Recommendation** | Fail-fast on startup if `venue_cfg.trading.hedge_mode` is false for hedge-grid strategy, and assert `config.oms_type == OmsType.HEDGING` at strategy `on_start()`. |

## 4) Risk Controls — Hot Path Verification

| Field | Details |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | Drawdown check is **not** at top of `on_bar()`; regime update/warmup logic runs first, violating intended gate order (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:692-758`). `_validate_order_size()` validates per-order notional vs free balance but does **not** include current side exposure + proposed order, so max position cap can be bypassed incrementally (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:2469-2514`). Circuit breaker is called from rejected/denied handlers (good), and cooldown exists (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:1358-1612,2515-2559`). |
| **Risk Level** | Critical |
| **Recommendation** | Move drawdown gate immediately after init checks in `on_bar()` before detector updates, and update `_validate_order_size()` to compute `(existing_position_notional + new_order_notional) <= balance * max_position_pct` per side. |

## 5) NautilusTrader Strategy Lifecycle Correctness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Lifecycle hooks exist and are reasonably complete (`on_start`, `on_bar`, `on_data`, order event handlers, `on_stop`). Flow mostly matches documented sequence but risk-gate ordering differs as above. `on_stop()` cancels open strategy orders but does not flatten positions despite doc warning (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:540-579`). |
| **Risk Level** | Medium |
| **Recommendation** | Align exact on-bar order with architecture docs; optionally add config flag for flatten-on-stop in live mode. |

## 6) Exchange API & Connectivity Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Testnet monkey patch is globally applied from base runner import path (`naut_hedgegrid/runners/base_runner.py:15-17`), and monkey-patches class method unconditionally (`naut_hedgegrid/adapters/binance_testnet_patch.py:10-39`). Warmup is non-blocking/fail-open (strategy continues), matching docs but can start cold when data fetch fails (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py` warmup section, `naut_hedgegrid/warmup/binance_warmer.py:120-173`). |
| **Risk Level** | Medium |
| **Recommendation** | Gate patching by explicit `testnet=True` runtime condition; add reconnect/data-gap telemetry and hard alerts for stale mark/funding streams. |

## 7) Operational Controls & Kill Switch

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Kill switch has proper breaker checks and flatten sequencing (`naut_hedgegrid/ops/kill_switch.py:260-515`). But `OperationsManager` instantiates default `KillSwitchConfig()` instead of loading environment/ops config, risking mismatched production thresholds (`naut_hedgegrid/ops/manager.py:95-104`). |
| **Risk Level** | High |
| **Recommendation** | Inject validated ops config object from runner CLI/config file; log active thresholds at startup and include checksum/config source in logs. |

## 8) FastAPI REST API Security

| Field | Details |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | API auth is optional by design (no key => unrestricted) (`naut_hedgegrid/ui/api.py:239-256`). Server binds `0.0.0.0` by default in both API and manager startup (`naut_hedgegrid/ui/api.py:463-500`, `naut_hedgegrid/ops/manager.py:80`). CORS allows `*` for all methods/headers (`naut_hedgegrid/ui/api.py:205-212`). No API rate limiting present. |
| **Risk Level** | Critical |
| **Recommendation** | Enforce mandatory auth in live mode, default bind to `127.0.0.1`, restrict CORS origins, and add request rate-limiting + audit logging for mutating endpoints. |

## 9) Security Audit

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Env var substitution for `${VAR}` works and errors on missing vars (`naut_hedgegrid/utils/yamlio.py:16-55`). No hardcoded exchange keys found in repo scan. SQL writes use parameterized queries in optimization DB (`naut_hedgegrid/optimization/results_db.py:170-240`). Dockerfile was not deeply validated in runtime execution here (static review recommended). |
| **Risk Level** | Medium |
| **Recommendation** | Add secret-scanning CI (gitleaks/trufflehog), enforce file permission checks for config files, and run dependency CVE audit in CI (`pip-audit`/`safety`). |

## 10) Concurrency & Race Conditions

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Strategy includes multiple locks (order IDs, fills, ladders, cache), acknowledging cross-thread interactions (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:118-170`). API callbacks execute in threadpool and access strategy state indirectly (`naut_hedgegrid/ui/api.py:214-238`). Ops manager reads private state directly, increasing race surface (`naut_hedgegrid/ops/manager.py:230-249`). |
| **Risk Level** | Medium |
| **Recommendation** | Consolidate all external strategy interactions through thread-safe command queue / actor mailbox and expose immutable snapshots only. |

## 11) Error Handling & Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Multiple broad `except Exception` blocks in hot paths can hide faults (e.g., `on_order_filled`, `on_data`, warmup, ops callbacks), with mixed handling strategy (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:919-938,939-1239`; `naut_hedgegrid/ops/manager.py:271-282`). Crash recovery includes grid-order cache hydration (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:581-607`) but key state remains in-memory. |
| **Risk Level** | Medium |
| **Recommendation** | Narrow exception classes, add structured error codes, and persist minimal recovery state (grid center, last regime, active fill keys) for restart continuity. |

## 12) Code Quality & Cleanliness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `strategy.py` is monolithic and high-complexity (2.6k+ lines) with many responsibilities. There are stale docs in ops API path references (README/CLAUDE mention `/api/v1/*` while current routes are top-level like `/flatten`) (`naut_hedgegrid/ui/api.py:263-460`, `CLAUDE.md:674-680`). |
| **Risk Level** | Medium |
| **Recommendation** | Split strategy into lifecycle coordinator + dedicated services (risk gate service, TP/SL manager, reconciliation service). Keep docs in sync via integration tests over OpenAPI schema. |

## 13) Configuration Validation

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Pydantic constraints are generally strong (`naut_hedgegrid/config/strategy.py`). However, policy mode enum includes `core-and-scalp` but runtime policy branch is not differentiated (logic/config mismatch). Live/testnet accidental crossover protection depends largely on operator-selected YAML, not strict environment guardrails. |
| **Risk Level** | Medium |
| **Recommendation** | Add cross-config validators: (a) hedge-grid strategy requires hedge_mode true, (b) live config cannot use testnet URLs and vice versa, (c) policy strategy must map to concrete runtime branch with tests. |

## 14) Testing Gaps

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Claimed baseline is 645 collected / 608 pass / 37 skipped (`README.md:485`, `CLAUDE.md:651`). In this environment, full suite cannot be collected due missing dependencies (`pydantic`, `numpy`, `fastapi`, `nautilus_trader`, `hypothesis`, `yaml`) (`pytest -q --disable-warnings -rs` output). No direct evidence in repo documents listing exactly which 37 are skipped and why. |
| **Risk Level** | Medium |
| **Recommendation** | Add CI artifact that publishes skipped test IDs + reasons (`pytest -rs -q` JSON report), and enforce non-empty justifications for each skip marker. |

## 15) Data Pipeline Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Data schemas/pipeline modules exist with tests, but full runtime validation not executed in this environment due missing deps. Warmup pagination for detector bars estimates pagination by elapsed intervals (approximation due no timestamp in `DetectorBar`), which can drift on long pulls (`naut_hedgegrid/warmup/binance_warmer.py:212-241`). |
| **Risk Level** | Medium |
| **Recommendation** | Carry timestamps through detector warmup path or use raw kline timestamps for deterministic pagination. Add corruption/fuzz tests for malformed inputs across pipeline boundaries. |

## 16) Performance

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Hot path does multiple allocations/logging per bar and repeated cache/order scans. Caches exist, but `on_bar` remains heavy and verbose (`naut_hedgegrid/strategies/hedge_grid_v1/strategy.py:692-894`). In-memory tracking structures are mostly bounded, but some can grow if not cleaned aggressively (e.g., retry/error tracking guarded but manual). |
| **Risk Level** | Medium |
| **Recommendation** | Reduce INFO logging frequency in hot path, precompute immutable config-derived constants, and profile bar loop with realistic feed rates. |

---

## Executive Summary

### Top 5 Critical Issues (Fix Before Going Live)
1. Risk gate order violation: drawdown check is not at the top of `on_bar()`.
2. Position validation underestimates exposure (order-only notional check).
3. API can be unauthenticated + internet-exposed by default (`0.0.0.0`, permissive CORS).
4. Hedge-mode can silently degrade to NETTING through runner config.
5. Policy config/runtime mismatch (`core-and-scalp` not truly implemented).

### Top 5 Robustness Improvements
1. Enforce HEDGING-only startup invariant in runners + strategy.
2. Harden API: mandatory key in live mode, localhost default bind, rate limiting.
3. Narrow broad exception handling and add structured fault policy.
4. Persist minimal restart state and improve reconciliation telemetry.
5. Make kill switch config source explicit and versioned.

### Top 5 Code Quality Improvements
1. Decompose `strategy.py` into smaller services.
2. Remove private-state coupling from Ops manager.
3. Align docs/endpoints and test OpenAPI contract in CI.
4. Implement true policy strategy branching.
5. Add explicit skipped-test reporting with reasons.

### Overall Health Score
**6.5 / 10** — Core design is solid, but live-trading safety hardening (risk gating, API exposure, hedge invariants) needs targeted fixes before production capital deployment.

### Prioritized Action Plan (Risk × Effort)
1. **(High/Low effort)** Enforce HEDGING-only in runner and strategy startup.
2. **(Critical/Medium)** Fix `_validate_order_size()` to include existing exposure.
3. **(Critical/Medium)** Move drawdown gate to very top of `on_bar()` and test it.
4. **(Critical/Medium)** Lock down API auth/bind/CORS/rate limiting.
5. **(High/Medium)** Implement policy mode branching + add tests.
6. **(Medium/Medium)** Refactor ops↔strategy interface to avoid private attr coupling.
7. **(Medium/High)** Break up monolithic strategy and profile hot path.
