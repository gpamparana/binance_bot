# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope reviewed (code + tests + runtime config):
- Core strategy orchestration: `naut_hedgegrid/strategies/hedge_grid_v1/strategy.py`
- Reusable strategy components: `naut_hedgegrid/strategy/{grid,policy,detector,funding_guard,order_sync}.py`
- Domain/exchange boundary: `naut_hedgegrid/domain/types.py`, `naut_hedgegrid/exchange/precision.py`
- Config/loading: `naut_hedgegrid/config/*.py`, `naut_hedgegrid/utils/yamlio.py`
- Runners + adapters + warmup: `naut_hedgegrid/runners/*.py`, `naut_hedgegrid/adapters/binance_testnet_patch.py`, `naut_hedgegrid/warmup/binance_warmer.py`
- Ops + API: `naut_hedgegrid/ops/*.py`, `naut_hedgegrid/ui/api.py`
- Optimization/data pipeline/security infra: `naut_hedgegrid/optimization/*.py`, `naut_hedgegrid/data/{schemas.py,pipelines/*}.py`, `docker/Dockerfile`, `pyproject.toml`, `README.md`
- Tests: `tests/**`

> Important environment note: full pytest execution could not be completed due missing dependencies (`pydantic`, `numpy`, `nautilus_trader`, `fastapi`, etc.), so skip-count validation relies on static test inspection + README claims.

---

## 1) Architecture & Layered Structure Integrity

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Layering mostly respected: strategy orchestrator delegates to component modules (`GridEngine`, `PlacementPolicy`, `RegimeDetector`, `FundingGuard`, `OrderDiff`). Domain types are used broadly (`Side`, `Ladder`, `Rung`, `OrderIntent`, `LiveOrder`). However, there is coupling drift in `strategy.py` (monolithic orchestration + ops controls + API callback surface + risk + reconciliation all in one file). `exchange/precision.py` imports domain `Rung` and acts as boundary guard correctly. No obvious circular import crash patterns found in reviewed modules. |
| **Risk Level** | Medium |
| **Recommendation** | Split `HedgeGridV1` into internal services (`RiskGateService`, `ExitAttachmentService`, `OrderExecutionService`, `OpsFacade`) and keep `on_bar` as pure pipeline coordinator. Add architecture tests enforcing allowed import directions. |

---

## 2) Trading Logic & Grid Engine Correctness

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `GridEngine.build_ladders()` correctly puts LONG below mid and SHORT above mid, with geometric qty scaling (`base_qty * qty_scale^(level-1)`). Recenter logic is simple and correct (`last_center==0 => True`, otherwise bps threshold). `PlacementPolicy` behavior matches docs for `throttled-counter`; `core-and-scalp` truncates both sides, scales only counter side. `RegimeDetector` computes EMA/ADX/ATR with hysteresis and warmup gate; ATR is computed but not used in regime decision. `FundingGuard` adjusts paying side near funding when projected cost exceeds max. `OrderDiff` uses tolerance and level+side matching, minimizing churn. **Critical mismatch**: TP/SL creators claim reduce-only but submit with `reduce_only=False`; this can allow unintended exposure increases if position_id mapping fails or exchange behavior diverges. |
| **Risk Level** | High |
| **Recommendation** | Introduce adapter-safe reduce-only mode fallback with explicit validation: use `reduce_only=True` where adapter supports hedge-side correctly, otherwise enforce pre-submit side-position checks + post-submit order audit. Add test that TP/SL can never increase absolute side exposure. |

---

## 3) Hedge Mode Position Management

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Position IDs are consistently built as `{instrument_id}-LONG/SHORT` in order submission paths and close paths. OmsType guard blocks non-hedging mode on start. Restart hydration exists and filters TP/SL from grid cache. **Risk**: `_create_tp_order/_create_sl_order` accept `position_id` arg but do not embed it in order object (only at `submit_order` callsite). Also, cache hydration relies on parsed client IDs and ignores malformed orders (safe fail, but can leave unmanaged exchange orders). |
| **Risk Level** | Medium |
| **Recommendation** | Remove unused `position_id` parameters from constructors or assert parity with submit position_id; add startup reconciliation report that enumerates unmanaged open orders and hard-fails in live mode if unmanaged count > 0. |

---

## 4) Risk Controls — Hot Path Verification

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Drawdown check is called at top of `on_bar()` before trading logic, and sets `_pause_trading`. Circuit breaker is triggered in both `on_order_rejected()` and `on_order_denied()`. `_validate_order_size` is called in `_execute_add()` before every grid submit. Config reads are mostly nested (`cfg.risk_management.*`, `cfg.position.*`). **Gaps**: drawdown uses account balance only (not explicit unrealized drawdown decomposition), and `_flatten_all_positions` / TP/SL/reconcile submissions bypass `_validate_order_size` (intentional for exits, but not uniformly documented/guarded). One `getattr(self, "_tp_sl_buffer_mult", ...)` pattern remains (low severity; not config-root bug, but avoid dynamic fallback on safety constants). |
| **Risk Level** | High |
| **Recommendation** | Make risk gate contract explicit: (1) entry orders require `_validate_order_size`, (2) exit orders exempt but must prove exposure-reducing invariant. Replace dynamic `getattr` fallback with strict initialized field. Add regression tests for known config-access bug pattern. |

---

## 5) NautilusTrader Strategy Lifecycle Correctness

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Lifecycle callbacks exist and mostly follow documented flow. `on_start()` initializes components, subscribes bars and mark-price data, hydrates cache, and warmup attempt is non-fatal. `on_bar()` ordering is close to desired pipeline. `on_data()` forwards funding updates. `on_stop()` cancels open strategy orders. **Issue**: several broad exception handlers downgrade failures to logs, potentially hiding lifecycle integrity problems in live mode (especially warmup/data subscription issues). |
| **Risk Level** | Medium |
| **Recommendation** | Add strict/live mode: in live mode, fail startup if critical subscriptions or component init fail. Keep permissive behavior only in backtest/paper. |

---

## 6) Exchange API & Connectivity Resilience

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Exchange interactions are through Nautilus adapter in runners/strategy. Testnet monkey-patch is idempotent and specific but globally monkey-patches class method (process-wide side effect). Warmup client uses pagination/rate-delay but hardcoded delay and no exponential backoff strategy for repeated 429/5xx. Data-gap/stale websocket handling is not strongly explicit at strategy level. |
| **Risk Level** | Medium |
| **Recommendation** | Scope testnet patch by environment guard and explicit activation flag; add robust retry/backoff in warmer + stale market-data watchdog in strategy (halt order placement on stale bars/funding stream). |

---

## 7) Operational Controls & Kill Switch

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `OperationsManager` wires Prometheus/API/KillSwitch and keeps running if one component fails (good resilience). KillSwitch has multiple triggers (drawdown/funding/margin/loss) and lock-protected trigger dedup. Alerts are best-effort and errors are logged. **Gaps**: kill switch flatten path depends on strategy callback return success semantics and may leave partial-close scenarios; no hard confirmation loop that positions actually reached zero after flatten. |
| **Risk Level** | High |
| **Recommendation** | Add post-flatten verification loop with timeout + escalation (re-issue IOC closes, alert critical if residual exposure remains). Persist last trigger context for postmortem even after restart. |

---

## 8) FastAPI REST API Security

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Write endpoints call `_validate_write_auth`; if no API key and `require_auth=True`, writes are denied (safe default). Read endpoints can be open when no key configured. API rate limiting exists (in-memory IP sliding window). Default bind host is localhost from `start_server`. **Risks**: in-memory limiter is process-local and non-persistent; thread-based uvicorn shutdown is incomplete (may leave zombie serving state during lifecycle transitions). |
| **Risk Level** | Medium |
| **Recommendation** | Require auth unconditionally in live mode by config validation; add explicit listen-interface checks in live startup (`127.0.0.1` only unless explicit override + warning). Use proper server lifespan/shutdown handling. |

---

## 9) Security Audit

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | No hardcoded API keys found in reviewed code. YAML env-substitution supports `${VAR}` and `${VAR:-default}`. Docker runs as non-root user and avoids baking secrets. Dependencies are version-pinned minimally but not locked by upper bounds; no evidence of automated CVE scanning workflow. SQLite usage in optimization uses parameterized queries in reviewed paths (low SQLi risk). Webhook URLs come from config and are used directly; misconfiguration could exfiltrate alerts but not execute code. |
| **Risk Level** | Medium |
| **Recommendation** | Add CI `pip-audit`/`safety` job, secret-scanning hooks, and runtime log scrubbing tests for sensitive headers/keys. Enforce restrictive file permissions for config files containing creds (`0600`). |

---

## 10) Concurrency & Race Conditions

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Strategy loop is event-driven, but API runs in separate thread and invokes callback via threadpool. Strategy adds locks around mutable shared state (`_order_id_lock`, `_fills_lock`, `_grid_orders_lock`, `_ladder_lock`). **Risk**: callback path still accesses strategy state concurrently; not all fields are lock-guarded (`_throttle`, `_pause_trading`, metrics snapshots). |
| **Risk Level** | High |
| **Recommendation** | Route API write actions onto strategy actor/event queue instead of direct method calls from API threads. Treat strategy state as single-thread-owned; API only enqueues commands and reads snapshots from immutable copy. |

---

## 11) Error Handling & Resilience

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Many `except Exception` blocks preserve uptime, but some may mask correctness issues (warmup, event handlers, API handlers). Critical paths sometimes fail-open (continue trading) instead of fail-safe. Crash recovery partially covered by order cache hydration and position reconcile; broader state is in-memory only. |
| **Risk Level** | High |
| **Recommendation** | Classify exceptions into recoverable vs non-recoverable; in live mode, escalate non-recoverable to trading pause. Persist minimal recovery state (last center, ladder version, trigger flags) to disk/DB. |

---

## 12) Code Quality & Cleanliness

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Good typing/dataclasses in many modules. Primary maintainability issue is complexity concentration in `strategies/hedge_grid_v1/strategy.py` (very long, many concerns). Some stale comments/docstrings (reduce-only wording vs actual flags). Magic constants still present (timing windows, fallback bps, cache limits) though many are config-backed. |
| **Risk Level** | Medium |
| **Recommendation** | Break strategy file into smaller modules and add architecture tests + complexity thresholds. Align docstrings/comments with actual behavior to avoid operator misunderstandings. |

---

## 13) Configuration Validation

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Pydantic models include sensible bounds and validators. Loader fails fast with detailed validation errors. OmsType hedge-mode checks exist at strategy start. **Gap**: environment separation safety (testnet vs live) depends on user config discipline; no hard mutual-exclusion guard that prevents accidental live trading with wrong venue file. |
| **Risk Level** | High |
| **Recommendation** | Add startup interlock requiring explicit `--confirm-live` plus venue consistency checks (e.g., `api.testnet` + runner mode + endpoint). Reject ambiguous configs. |

---

## 14) Testing Gaps

| Field | Assessment |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | In this environment, test run failed during collection due missing dependencies; therefore critical-path confidence is reduced. Static scan shows explicit skips in `tests/data/test_pipeline.py`, `tests/test_parity.py`, `tests/test_ops_integration.py`, and runtime `pytest.skip` in strategy smoke tests. README claim of `645 collected / 37 skipped` could not be revalidated here. No clear dedicated regression test found for config-root `getattr` misuse pattern. |
| **Risk Level** | Critical |
| **Recommendation** | Enforce CI matrix with full dependency set and publish skip inventory artifact per build. Add hard-required tests for: hedge-mode position IDs, TP/SL side correctness, drawdown gate top-of-bar ordering, circuit breaker cooldown, crash restart reconciliation, config-access regression. |

---

## 15) Data Pipeline Integrity

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Data schemas and normalization pipeline are present with validation intent. Potential risk areas are timestamp normalization consistency, duplicate handling, and gap behavior under malformed feeds. Need stronger end-to-end corruption tests across CSV/WebSocket/Tardis sources. |
| **Risk Level** | Medium |
| **Recommendation** | Add strict schema + checksum validations at ingest boundaries and fail fast on malformed timestamps in live ingestion. Expand property tests for out-of-order/gapped/corrupt inputs. |

---

## 16) Performance

| Field | Assessment |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Hot path is generally efficient (diff caching, order cache). However, `on_bar` does substantial logging and multi-stage transforms; live performance may degrade under verbose logs. In-memory maps/sets are bounded in some places but not all runtime histories are explicitly bounded. |
| **Risk Level** | Medium |
| **Recommendation** | Introduce log sampling in hot path, benchmark `on_bar` latency budget, and add telemetry for per-stage timing. Ensure all long-lived collections are bounded or periodically compacted. |

---

# Executive Summary

## Top 5 Critical Issues (fix before live)
1. **TP/SL submitted with `reduce_only=False` despite safety intent**, increasing side-effect risk during hedge anomalies.
2. **Threaded API ↔ strategy shared state access** can race without strict actor-queue mediation.
3. **Test confidence gap**: full test suite not currently reproducible in this environment; skip inventory not strongly governed.
4. **Risk invariants not fully formalized for non-grid submits** (reconcile/flatten/exit paths).
5. **Environment interlocks insufficient** to prevent operator misconfiguration between testnet/live.

## Top 5 Robustness Improvements
1. Add strict live-mode startup checks for subscriptions, auth, and environment consistency.
2. Add post-flatten verification loop with residual exposure escalation.
3. Implement stale-data watchdog (bars/funding freshness) that pauses entries.
4. Persist minimal strategy recovery state for crash continuity.
5. Add resilient retry/backoff policy for warmup/API calls with explicit 429 handling.

## Top 5 Code Quality Improvements
1. Decompose `strategy.py` into focused services.
2. Remove stale comments/docs mismatching runtime behavior.
3. Eliminate dynamic safety fallbacks (`getattr`) in critical math constants.
4. Add architecture import-boundary tests.
5. Add complexity and layering lint gates in CI.

## Overall Health Score
**6.5 / 10**

Justification: design is strong and feature-rich with substantial safeguards, but live-trading criticality demands tighter concurrency discipline, stronger invariant testing, and stricter operational interlocks before production capital exposure.

## Prioritized Action Plan (risk × effort)
1. **High risk / low-medium effort:** enforce reduce-only-safe exit invariant + tests.
2. **High risk / medium effort:** serialize API writes onto strategy event queue.
3. **High risk / medium effort:** add live-mode startup interlocks (auth + endpoint + testnet/live consistency).
4. **High risk / medium effort:** implement post-flatten residual-position verification.
5. **Medium risk / medium effort:** split strategy orchestration into smaller modules.
6. **Medium risk / low effort:** harden logging and collection bounds in hot path.
7. **Medium risk / medium effort:** expand integration chaos tests (disconnects, spikes, drawdown breach, CB activation).
