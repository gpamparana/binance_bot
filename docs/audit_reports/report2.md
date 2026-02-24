# naut-hedgegrid Comprehensive Code Audit (2026-02-21)

Scope: `naut_hedgegrid/`, `tests/`, `configs/`, `docker/`, and top-level packaging/config files.
Method: static file-by-file review + targeted command-based checks.

## 1) Architecture & Layered Structure Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Layering is mostly respected: reusable modules (`strategy/`, `domain/`, `exchange/`) are consumed by concrete strategy (`strategies/hedge_grid_v1/strategy.py`). No obvious reverse imports from `domain` into strategy/orchestration layers were found via import scan. However, `strategy.py` now embeds many operational and API-adjacent concerns (flatten ops, metrics, retry/circuit controls), diluting Strategy→Component separation. `OperationsManager` directly manipulates strategy internals (`_throttle`, snapshots) through privileged callback routing, tightening coupling across Strategy↔Ops boundaries. |
| **Risk Level** | Medium |
| **Recommendation** | Extract `RiskGateService`, `ExecutionService`, and `PositionLifecycleService` from `strategies/hedge_grid_v1/strategy.py`; keep orchestration in `on_bar()` and event handlers. Move `flatten_side`/snapshot methods behind explicit strategy interface protocol consumed by ops to avoid private-state coupling. |

## 2) Trading Logic & Grid Engine Correctness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `GridEngine` correctly places LONG below mid and SHORT above mid with geometric qty scaling and Decimal-first math. `PlacementPolicy` appears consistent for `throttled-counter` and `core-and-scalp` behavior. Regime detector uses EMA spread + ADX threshold + hysteresis and warmup checks. Funding guard wiring exists and uses mark price updates. Critical issue: `on_order_filled()` realized PnL calculation reads **current** cached position avg price when TP/SL fills, which can drift from lot-level entry at fill time and can misstate realized PnL in partial/overlapping fills. |
| **Risk Level** | High |
| **Recommendation** | Track per-fill entry basis at open-fill time and compute realized PnL using that immutable basis; avoid querying mutable current position avg for exit accounting. Add regression tests for partial fills and interleaved LONG/SHORT exits. |

## 3) Hedge Mode Position Management

| Field | Details |
|---|---|
| **Status** | 🟢 Good |
| **Findings** | Position IDs consistently follow `{instrument_id}-LONG`/`-SHORT` in order submission and reconciliation paths. Grid adds use side-specific `position_id`, and TP/SL creation uses side-derived opposite order side while preserving side-specific position ID. `OmsType.HEDGING` is explicitly enforced at startup and pauses strategy on mismatch. Cache hydration on startup exists to avoid duplicate initial grid placements. |
| **Risk Level** | Low |
| **Recommendation** | Keep current pattern; add explicit integration test for restart + hydrated open orders + no duplicate adds on first bar. |

## 4) Risk Controls — Hot Path Verification

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Drawdown gate is called at top of `on_bar()` before trading pipeline. Circuit breaker is called from both rejection and denial handlers. Position validation is enforced in `_execute_add()` before submit. Config access uses nested fields (`risk_management`, `position`). Gap: TP/SL submission path in `on_order_filled()` bypasses `_validate_order_size()` (arguably reduce-only, but still a bypass). Drawdown computes from account total balance and does not explicitly include separate unrealized drawdown decomposition requested by spec. |
| **Risk Level** | Medium |
| **Recommendation** | Add explicit bypass rationale in code comments for reduce-only exits; or run a reduced validation path for exits to confirm reduce-only and side-safe. Add test ensuring all non-exit order submissions route via `_execute_add()` gate. |

## 5) NautilusTrader Strategy Lifecycle Correctness

| Field | Details |
|---|---|
| **Status** | 🟢 Good |
| **Findings** | Lifecycle hooks are present and generally correct: `__init__` mostly state-only, `on_start` loads config/components/subscriptions/hydration/warmup, `on_bar` follows documented sequence (risk→detector→grid→policy→funding→throttle→diff→execute), `on_data` updates funding guard, order event handlers track state, `on_stop` cancels open strategy orders. |
| **Risk Level** | Low |
| **Recommendation** | Add sequence-level integration test asserting `on_bar` call-order invariants using instrumentation mocks. |

## 6) Exchange API & Connectivity Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Warmup client uses pagination and request delay. Testnet patch is isolated in adapter module and idempotent. However: no explicit 429/backoff branching in warmer (generic HTTPError only), no stale-data/gap watchdog in live event path, and warmup failures are non-blocking (strategy continues), which can cause prolonged no-trade/wrong-regime startup behavior if detector remains cold. |
| **Risk Level** | Medium |
| **Recommendation** | Add explicit handling for 429 with exponential backoff + jitter; add data-gap monitor in strategy (`last_bar_age` guard). On warmup failure, either force SAFE mode until warm or make behavior config-controlled (`require_warmup_success`). |

## 7) Operational Controls & Kill Switch

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Ops manager wires Prometheus + API + kill switch and degrades gracefully on failures. Kill switch monitors drawdown/funding/margin/loss and calls strategy flatten path with alerts when available. Concern: `OperationsManager` currently instantiates `KillSwitch(..., alert_manager=None)`, so configured Slack/Telegram alerts are not actually wired through manager by default. |
| **Risk Level** | High |
| **Recommendation** | Build `AlertManager` from `OperationsConfig.alerts` in `OperationsManager.start()` and pass to `KillSwitch`. Add startup log asserting alert channels enabled/disabled. |

## 8) FastAPI REST API Security

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | API key auth logic is strong for write endpoints by default (`require_auth=True` blocks writes if key missing). Input models are validated with Pydantic and includes rate limiting middleware. API binds localhost by default. Risk remains if runner starts API on `0.0.0.0` without strong key/network controls; no per-endpoint RBAC and no replay/nonce controls. |
| **Risk Level** | Medium |
| **Recommendation** | Enforce auth for both read/write in live mode; disallow `0.0.0.0` bind in live unless explicit override + warning. Add optional IP allowlist. |

## 9) Security Audit

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | No hardcoded exchange keys found in reviewed sources/config templates; YAML env substitution supports `${VAR}` and `${VAR:-default}`. Dockerfile uses non-root user in runtime stage. Potential concern: dependency versions are mostly minimum-bounded (not pinned), making supply/CVE posture environment-dependent; local CVE audit could not be completed in this container. SQL writes in optimization DB mostly parameterized, but one query in `get_best_trials` interpolates a controlled string fragment (`validity_filter`)—currently safe due boolean-controlled branch. |
| **Risk Level** | Medium |
| **Recommendation** | Add lockfile-driven production install policy and scheduled `pip-audit` in CI. Keep SQL building constrained to known literals or build full parameterized branches separately for clarity. |

## 10) Concurrency & Race Conditions

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Strategy is event-driven, but ops/API run in separate threads. Code uses multiple locks (`_grid_orders_lock`, `_fills_lock`, `_ladder_lock`), which is good. Still, cross-thread callbacks (`flatten_side`, `set_throttle`) mutate strategy state outside actor event loop; comments claim cache thread-safety, but there is no explicit actor-thread marshalling for all operations. |
| **Risk Level** | High |
| **Recommendation** | Route all mutating API/kill-switch actions through Nautilus actor-safe queue/timer callback on strategy thread. Keep API thread read-only where possible. |

## 11) Error Handling & Resilience

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Many exceptions are caught and logged to keep bot running (good for availability), but broad catches in hot paths can mask logic defects and silently degrade behavior (e.g., warmup, fill handling, data handling). Crash recovery has cache hydration, but most critical state remains in-memory only (fills/retries/error windows). |
| **Risk Level** | Medium |
| **Recommendation** | Narrow exception scopes and classify recoverable vs programming errors. Persist minimal critical state snapshot (e.g., exits-attached map, breaker state) for cleaner restart semantics. |

## 12) Code Quality & Cleanliness

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | `strategy.py` is very large and multifaceted (orchestration + risk + TP/SL + ops + diagnostics), increasing cognitive load. Several `# type: ignore` suppressions are present in strategy/order paths. Some magic constants remain (retry counts, log intervals). |
| **Risk Level** | Medium |
| **Recommendation** | Split strategy into focused collaborators and reduce `type: ignore` via stronger typing wrappers. Externalize timing/magic constants to config where practical. |

## 13) Configuration Validation

| Field | Details |
|---|---|
| **Status** | 🟢 Good |
| **Findings** | Pydantic v2 models include substantial bounds/validators for grid/risk/regime/position settings and fail-fast loader errors are clear. Venue configs distinguish production/testnet and indicate hedge mode intent. |
| **Risk Level** | Low |
| **Recommendation** | Add explicit startup assertion tying venue `trading.hedge_mode` + strategy `oms_type` + runtime adapter mode to prevent accidental environment mismatch. |

## 14) Testing Gaps

| Field | Details |
|---|---|
| **Status** | 🔴 Critical Issue |
| **Findings** | In this environment, pytest collection failed due missing dependencies, so full pass/skip counts (e.g., claimed 645/608/37) could not be verified. Static scan found several explicit skips in parity, ops integration, and data pipeline tests, including parity tests marked skipped due config API drift. This implies regression blind spots in parity/determinism and ops threading behavior. |
| **Risk Level** | Critical |
| **Recommendation** | Restore CI test environment and enforce parity tests non-skipped. Add dedicated tests for known config access bug pattern, crash restart hydration, and live-mode auth enforcement. |

## 15) Data Pipeline Integrity

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Schemas provide useful validation and timezone normalization. Normalizer/pipeline coverage exists but pipeline includes optional dependency skips and mixed-source assumptions. Corrupted source rows may still pass into partial outputs depending on source-specific normalizers if not fully strict. |
| **Risk Level** | Medium |
| **Recommendation** | Add strict row-level quarantine/error counters and fail-threshold controls per batch. Emit data quality metrics (gaps, duplicates, dropped rows) for ops observability. |

## 16) Performance

| Field | Details |
|---|---|
| **Status** | 🟡 Needs Improvement |
| **Findings** | Hot path uses diffing and cache optimizations, but strategy still does substantial work/logging each bar. In `parallel_runner`, duration accounting is inaccurate (`start_time` set after future completion), impairing optimization telemetry quality. Some in-memory structures can grow (retries, error windows mitigated partially). |
| **Risk Level** | Medium |
| **Recommendation** | Reduce per-bar log verbosity in non-debug live mode, precompute reusable values, and fix parallel runner timing by capturing submit/start timestamps per task. Add bounded/aged cleanup policies where missing. |

---

## Executive Summary

### Top 5 Critical Issues (fix before live)
1. **Test coverage blind spots are real now**: parity/determinism and some ops tests are skipped or unverified in current environment.
2. **Cross-thread strategy mutation risk** from API/ops without strict actor-thread marshalling.
3. **KillSwitch alerts not wired by default** in `OperationsManager` (`alert_manager=None`).
4. **Realized PnL accounting can be inaccurate** due using mutable cached position average at exit fill time.
5. **Warmup failure is non-blocking without explicit safe-mode policy**, potentially trading with insufficient context.

### Top 5 Robustness Improvements
1. Actor-thread command queue for flatten/throttle and all state mutations.
2. Backoff/retry with explicit 429 handling and stale-data watchdog.
3. Persist minimal recovery state across restarts.
4. Restore non-skipped parity and crisis-path integration tests.
5. Wire alert manager fully and test alert delivery failures as non-fatal.

### Top 5 Code Quality Improvements
1. Decompose `strategies/hedge_grid_v1/strategy.py` into smaller services.
2. Remove/replace `# type: ignore` suppressions in core execution flows.
3. Consolidate magic constants into config.
4. Strengthen interface boundaries between strategy and ops callback layer.
5. Add architecture tests for import-direction rules.

### Overall Health Score
**6.8 / 10** — Core trading and hedge-mode mechanics are thoughtfully implemented with many safety checks, but production reliability is constrained by concurrency coupling, test blind spots, and a few high-impact operational gaps.

### Prioritized Action Plan (risk × effort)
1. **P0 (High risk, Medium effort):** Reinstate full CI test environment and unskip/refactor parity + ops integration tests.
2. **P0 (High risk, Medium effort):** Marshal API/kill-switch mutations onto strategy actor thread.
3. **P1 (High risk, Low effort):** Wire alert manager into `OperationsManager` and validate channel fallbacks.
4. **P1 (Medium risk, Medium effort):** Rework realized PnL attribution to lot/fill basis.
5. **P1 (Medium risk, Low effort):** Add explicit warmup failure mode (`require_warmup_success`) and stale-data guard.
6. **P2 (Medium risk, Medium effort):** Refactor monolithic `strategy.py` into services.
7. **P2 (Low risk, Low effort):** Improve dependency security posture with scheduled CVE audits and pinned lockfile deployment policy.

## Commands Run
- `rg --files -g 'AGENTS.md'`
- `find . -maxdepth 3 -type f | sed 's#^./##' | head -n 300`
- `nl -ba ...` (targeted source inspections across strategy/config/ops/ui/data/optimization/docker)
- `rg -n "@pytest\.mark\.skip|skipif|pytest\.skip" tests`
- `pytest -q --maxfail=1 -rs` (failed: missing local deps)
- `uv run pytest -q -rs ...` (failed: missing local deps)
- attempted `uvx pip-audit` (did not complete in this environment)
