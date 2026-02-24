# Comprehensive Code Audit Report

**Project**: naut-hedgegrid — Hedge-mode grid trading system on NautilusTrader for Binance Futures
**Audit Date**: 2026-02-23
**Auditors**: 6 parallel specialized agents (Claude Opus 4.6)
**Scope**: 63 source files, 42 test files, full architecture review
**Overall Health Score**: 6.5 / 10

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Overall Health Score](#overall-health-score)
3. [Area 1: Architecture and Layered Structure](#area-1-architecture-and-layered-structure)
4. [Area 2: Trading Logic and Grid Engine](#area-2-trading-logic-and-grid-engine)
5. [Area 3: Hedge Mode Position Management](#area-3-hedge-mode-position-management)
6. [Area 4: Risk Controls — Hot Path](#area-4-risk-controls--hot-path)
7. [Area 5: NautilusTrader Lifecycle](#area-5-nautilustrader-lifecycle)
8. [Area 6: Exchange and Connectivity Resilience](#area-6-exchange-and-connectivity-resilience)
9. [Area 7: Operational Controls and Kill Switch](#area-7-operational-controls-and-kill-switch)
10. [Area 8: FastAPI REST API Security](#area-8-fastapi-rest-api-security)
11. [Area 9: Security](#area-9-security)
12. [Area 10: Concurrency and Race Conditions](#area-10-concurrency-and-race-conditions)
13. [Area 11: Error Handling and Resilience](#area-11-error-handling-and-resilience)
14. [Area 12: Code Quality](#area-12-code-quality)
15. [Area 13: Configuration Validation](#area-13-configuration-validation)
16. [Area 14: Testing Gaps](#area-14-testing-gaps)
17. [Area 15: Data Pipeline](#area-15-data-pipeline)
18. [Area 16: Performance](#area-16-performance)
19. [Prioritized Action Plan](#prioritized-action-plan)

---

## Executive Summary

The codebase demonstrates strong architectural fundamentals: a clean layered design, proper separation of concerns, excellent pure-functional components, and thorough Pydantic configuration validation. However, several critical issues must be addressed before live trading can be safely enabled.

### Critical Blockers (Live Trading Not Permitted)

- **Paper trading CLI defaults to the production venue config** — could place real orders against live exchange during what the operator believes is a paper session (`cli.py:243`)
- **`on_start()` silently continues after config load failure** without setting `_critical_error`, meaning the strategy may run in a half-initialized state (`strategy.py:223-225`)
- **All 27 strategy integration smoke tests are permanently skipped** — there is zero lifecycle coverage in the test suite (`tests/strategy/test_strategy_smoke.py`)
- **Kill switch startup failure is silently swallowed** in live mode, leaving the system unprotected (`ops/manager.py:146-148`)
- **AlertManager async code is broken on Python 3.12+** — `asyncio.get_event_loop()` raises a `DeprecationWarning`/`RuntimeError` in threads, causing alerts to be silently dropped (`ops/alerts.py:136-146`)
- **PrecisionGuard uses float arithmetic** — floating-point rounding can produce prices and quantities that fail exchange validation (`exchange/precision.py:129,152`)

**Verdict**: The system is NOT ready for live trading in its current state. Estimated 2-3 days of remediation are needed to clear the critical path.

---

## Overall Health Score

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| Architecture | 9/10 | Excellent layered design, clean dependency graph |
| Trading Logic | 8/10 | Sound grid math, proper Decimal usage in core |
| Risk Controls | 7/10 | Good hot-path gates, but timer callback bypass exists |
| Testing | 4/10 | Strong unit tests, zero integration/lifecycle coverage |
| Production Safety | 4/10 | Paper CLI points to prod, silent failures in ops layer |
| Code Quality | 6/10 | Well-documented but strategy file is oversized |
| Security | 7/10 | Good defaults, no hardcoded secrets |
| Performance | 7/10 | Acceptable for current scale with bounded caches |
| **Overall** | **6.5/10** | |

---

## Area 1: Architecture and Layered Structure

**Status**: GOOD

The import graph is clean and respects the intended layering: Strategy → Component → Domain → Exchange → Config → Runner → Ops → UI. No circular dependencies were detected during the audit.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Layer dependencies clean, no circular imports | GOOD | Low | All files |
| `strategy.py` correctly delegates to pure components | GOOD | Low | `strategies/hedge_grid_v1/strategy.py` |
| Domain types (`Side`, `Regime`, `Rung`, `Ladder`) used consistently | GOOD | Low | All component files |
| `PrecisionGuard` sits correctly between domain and Nautilus layers | GOOD | Low | `exchange/precision.py` |
| `strategies/` vs `strategy/` separation maintained | GOOD | Low | Both directories |
| Config layer uses Pydantic v2 API correctly throughout | GOOD | Low | `config/` |

No action items for this area.

---

## Area 2: Trading Logic and Grid Engine

**Status**: GOOD with 4 issues

The core grid math is sound. `build_ladders()`, `recenter_needed()`, `PlacementPolicy`, `RegimeDetector`, `FundingGuard`, and `OrderDiff` all produce correct results. TP/SL attachment uses the correct opposing side and preserves the position ID suffix.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| `GridEngine.build_ladders()` produces correct LONG/SHORT levels | GOOD | Low | `strategy/grid.py:83-175` |
| Geometric `qty_scale` math correct | GOOD | Low | `strategy/grid.py:110` |
| `recenter_needed()` logic correct | GOOD | Low | `strategy/grid.py:218-244` |
| `PlacementPolicy` throttled-counter and core-and-scalp correct | GOOD | Low | `strategy/policy.py:49-139` |
| `RegimeDetector` EMA/ADX/ATR calculations correct | GOOD | Low | `strategy/detector.py` |
| Hysteresis prevents regime flip-flop correctly | GOOD | Low | `strategy/detector.py:396-411` |
| `FundingGuard` tracking and adjustment correct | GOOD | Low | `strategy/funding_guard.py` |
| `OrderDiff` produces minimal operations with caching | GOOD | Low | `strategy/order_sync.py:170-269` |
| TP/SL attachment uses correct opposite side and position ID | GOOD | Low | `strategy.py:1907,1980` |
| **PrecisionGuard uses float arithmetic (not Decimal)** | CRITICAL | Medium | `exchange/precision.py:129,152` |
| `GridEngine` hardcodes `Decimal("0.01")` quantization | NEEDS IMPROVEMENT | Medium | `strategy/grid.py:86,96,105,144,158,163` |
| Asymmetric TP/SL floor logic — SHORT SL has no ceiling | NEEDS IMPROVEMENT | Medium | `strategy/grid.py:101-104` vs `161-163` |
| Division-by-zero risk in diagnostic logging | CRITICAL | Medium | `strategy.py:880` |

### Details

**PrecisionGuard float arithmetic** (`exchange/precision.py:129,152`): The `clamp_price()` and `clamp_qty()` methods use Python float operations to round to tick/step size. Floating-point representation errors can produce prices like `29999.9999999` instead of `30000.0`, which will be rejected by the exchange. These methods must be rewritten using `decimal.Decimal` with `ROUND_DOWN` quantization.

**Division-by-zero in diagnostic logging** (`strategy.py:880`): A logging statement computes a ratio without guarding against a zero denominator. Under normal startup conditions (zero total levels on the first bar) this will raise a `ZeroDivisionError` and crash the event loop.

**Asymmetric TP/SL floor** (`strategy/grid.py:101-104` vs `161-163`): LONG SL is floored at `price * 0.5` to prevent negative prices, but the equivalent ceiling guard is absent for SHORT SL. While less likely to trigger in practice, the asymmetry is a latent defect.

---

## Area 3: Hedge Mode Position Management

**Status**: GOOD with 2 issues

Hedge mode position ID construction is consistent across all nine call sites. LONG/SHORT TP/SL isolation is correct, and `submit_order()` always receives a `position_id`. Startup reconciliation via `_hydrate_grid_orders_cache()` correctly prevents duplicate ladder placements after restarts. `OmsType.HEDGING` is enforced during `on_start()`.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Position ID construction consistent across all 9 call sites | GOOD | Low | `strategy.py:651,1179,1831,2318,2405,2430,2486,2580` |
| LONG/SHORT TP/SL isolation correct | GOOD | Low | `strategy.py:1907,1980,1260-1261` |
| `submit_order()` always passes `position_id` | GOOD | Low | `strategy.py:728,1260,1834,2503` |
| `_hydrate_grid_orders_cache()` correctly prevents duplicates | GOOD | Low | `strategy.py:594-633` |
| `OmsType.HEDGING` enforced in `on_start()` | GOOD | Low | `strategy.py:228-237` |
| **Runner silently falls back to NETTING if `hedge_mode=false`** | NEEDS IMPROVEMENT | High | `base_runner.py:473`, `run_backtest.py:301` |
| `reduce_only=False` on all TP/SL/flatten orders | NEEDS IMPROVEMENT | Medium | `strategy.py:1946,2047,2500` |

### Details

**Silent NETTING fallback** (`base_runner.py:473`, `run_backtest.py:301`): When a venue config has `hedge_mode: false`, both runners silently downgrade to `OmsType.NETTING` rather than raising a configuration error. The strategy assumes hedge mode throughout; running it in NETTING mode produces undefined behavior. The runners should fail-fast with a clear error message.

**`reduce_only=False` on TP/SL orders** (`strategy.py:1946,2047,2500`): All TP, SL, and emergency flatten orders have `reduce_only=False`. This means they can open new positions rather than purely closing existing ones, particularly if two fills arrive close together. These should be set to `reduce_only=True`.

---

## Area 4: Risk Controls — Hot Path

**Status**: GOOD with 3 issues

Drawdown protection, peak balance tracking, the circuit breaker, and position size validation are all correctly implemented and wired into the trading hot path. The `getattr()` root-config anti-pattern (a known regression from a prior audit) is absent throughout.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Drawdown check at top of every `on_bar()` | GOOD | Low | `strategy.py:753-762` |
| Peak balance tracking correct | GOOD | Low | `strategy.py:2686-2692` |
| Circuit breaker triggered from both rejected and denied events | GOOD | Low | `strategy.py:1630-1632,1668` |
| Circuit breaker cooldown enforced correctly | GOOD | Low | `strategy.py:2656-2658` |
| `_validate_order_size` computes existing + pending + new | GOOD | Low | `strategy.py:2579-2608` |
| `getattr()` on root config anti-pattern absent | GOOD | Low | All verified |
| **Timer callback bypasses risk gates** | NEEDS IMPROVEMENT | Medium | `strategy.py:1613-1616` |
| Error window labeled "per_minute" but window is configurable | NEEDS IMPROVEMENT | Medium | `config/strategy.py:226` vs `244` |
| `on_order_filled` checks `_critical_error` but not `_pause_trading` | NEEDS IMPROVEMENT | Low | `strategy.py:1013` |

### Details

**Timer callback bypass** (`strategy.py:1613-1616`): The retry timer callback invokes `_execute_add()` directly without first checking `_pause_trading` or `_circuit_breaker_active`. This means a paused strategy can still submit orders through the retry path, defeating the drawdown gate.

**Misleading error window label** (`config/strategy.py:226` vs `244`): The field is documented as `max_errors_per_minute` but the actual window size is a separate configurable parameter. The field name creates a false expectation that the window is always one minute.

---

## Area 5: NautilusTrader Lifecycle

**Status**: NEEDS IMPROVEMENT

The `on_bar()` orchestration order correctly matches the documented flow (risk checks → regime → grid build → policy → funding → throttle → diff → execute). `on_data()` correctly feeds the `FundingGuard`. However, several lifecycle boundary conditions are not handled safely.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| `on_bar()` orchestration order matches documented flow | GOOD | Low | `strategy.py:735-949` |
| `on_data()` correctly feeds `FundingGuard` | GOOD | Low | `strategy.py:960-990` |
| **`on_start()` silently returns on config failure without setting `_critical_error`** | CRITICAL | Critical | `strategy.py:223-225` |
| **`on_start()` instrument not found returns without setting error flags** | CRITICAL | Medium | `strategy.py:241-243` |
| **Blocking HTTP warmup inside Nautilus event loop** | NEEDS IMPROVEMENT | Medium | `strategy.py:364-506`, `binance_warmer.py:80` |
| `on_order_filled()` re-raises exceptions, can crash the node | NEEDS IMPROVEMENT | Medium | `strategy.py:1289-1290` |
| Circuit breaker counts retried rejections as new errors | NEEDS IMPROVEMENT | Medium | `strategy.py:1630-1632` |
| `on_stop()` leaves positions unprotected — no close, no TP/SL | NEEDS IMPROVEMENT | Medium | `strategy.py:564-592` |
| Missing lifecycle methods: `on_reset`, `on_dispose`, `on_degrade` | NEEDS IMPROVEMENT | Medium | `strategy.py` |
| Grid center initialized to `0.0` — fragile on first bar | NEEDS IMPROVEMENT | Medium | `strategy.py:101` |

### Details

**Silent `on_start()` failure** (`strategy.py:223-225`): When the Pydantic config loader raises an exception during `on_start()`, the exception is caught and logged but `_critical_error` is never set to `True`. The strategy continues as if nothing happened, using uninitialized state. This must set `_critical_error = True` and call `self.stop()` immediately.

**Instrument not found** (`strategy.py:241-243`): Similarly, when the instrument lookup returns `None`, the method returns early without flagging the strategy as unhealthy. Subsequent bar events will raise `AttributeError` on `NoneType`.

**Blocking HTTP in event loop** (`strategy.py:364-506`, `binance_warmer.py:80`): The warmup routine issues synchronous HTTP requests via the Nautilus event loop thread. If the Binance API is slow or rate-limited, this blocks all market data processing for the duration. Warmup should be moved to the runner layer before the event loop starts, or executed in a thread pool.

**`on_order_filled()` re-raises** (`strategy.py:1289-1290`): A bare `raise` at the end of an exception handler in `on_order_filled()` will propagate to the Nautilus event loop and crash the live node. The exception should be caught, logged, and the strategy should transition to a degraded state.

---

## Area 6: Exchange and Connectivity Resilience

**Status**: NEEDS IMPROVEMENT with 2 CRITICAL issues

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| **Paper CLI default venue config points to production** | CRITICAL | Critical | `cli.py:243` |
| **`PaperRunner` with production config places real orders** | CRITICAL | Critical | `base_runner.py:805-824` |
| Live command has confirmation prompt | GOOD | Low | `cli.py:418-425` |
| Execution reconciliation correctly scoped | GOOD | Low | `base_runner.py:330-338` |
| Testnet patch has no unpatch mechanism | NEEDS IMPROVEMENT | Medium | `adapters/binance_testnet_patch.py:47` |
| No data gap detection between bars | NEEDS IMPROVEMENT | Medium | `strategy.py` |
| `BacktestRunner` disposes engine before extracting results | NEEDS IMPROVEMENT | High | `run_backtest.py:437-449` |
| `enable_ops` defaults to `False` for live trading | NEEDS IMPROVEMENT | Medium | `cli.py:332-335` |
| Dead code in warmup pagination | NEEDS IMPROVEMENT | Low | `binance_warmer.py:233` |

### Details

**Paper CLI default to production** (`cli.py:243`): The `paper` CLI subcommand defaults `--venue-config` to `configs/venues/binance_futures.yaml` — the production venue configuration. Any operator who runs `python -m naut_hedgegrid paper --strategy-config ...` without explicitly specifying a venue config will connect to the live Binance Futures exchange and may receive real market data subscriptions and, depending on implementation, submit real orders. The default must be changed to `configs/venues/binance_futures_testnet.yaml`.

**`BacktestRunner` dispose before extract** (`run_backtest.py:437-449`): `engine.dispose()` is called on line 437, which frees all internal state, before `_extract_results()` is called on line 449. Any result extraction that queries engine state after disposal will return empty or raise. The order must be reversed.

**`enable_ops` defaults to `False` for live** (`cli.py:332-335`): The `--enable-ops` flag is optional and defaults to `False` for the `live` subcommand. Operating live without the kill switch, Prometheus metrics, and alert system active is unsafe. This should default to `True` for live mode, or at minimum emit a prominent warning when live mode is started without ops enabled.

---

## Area 7: Operational Controls and Kill Switch

**Status**: NEEDS IMPROVEMENT

The `OperationsManager` correctly wires together the kill switch, alert system, and Prometheus exporter. However, several failure modes in this safety-critical layer are handled too permissively.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| `OperationsManager` correctly wires components | GOOD | Low | `ops/manager.py:66-151` |
| Prometheus uses isolated registry and update lock | GOOD | Low | `ops/prometheus.py:65,284` |
| **Kill switch startup failure silently swallowed in live mode** | NEEDS IMPROVEMENT | High | `ops/manager.py:146-148` |
| Race condition in `_trigger_circuit_breaker` lock release | NEEDS IMPROVEMENT | Medium | `ops/kill_switch.py:490-527` |
| Drawdown calculated vs PnL peak, not account balance | NEEDS IMPROVEMENT | Medium | `ops/kill_switch.py:353-374` |
| Post-flatten verification blocks monitoring thread for 6 seconds | NEEDS IMPROVEMENT | Medium | `ops/kill_switch.py:246-279` |
| `AlertManager` async integration broken in kill switch thread | NEEDS IMPROVEMENT | High | `ops/alerts.py:136-146` |
| Prometheus binds to all interfaces (`0.0.0.0`) | NEEDS IMPROVEMENT | Medium | `ops/prometheus.py:215` |

### Details

**Kill switch startup failure** (`ops/manager.py:146-148`): When `KillSwitch.start()` raises an exception (e.g., a thread spawn failure), the exception is caught, logged at WARNING level, and execution continues. In live trading, operating without the kill switch active means there is no automated circuit breaker. This must be a fatal error in live mode.

**AlertManager async broken** (`ops/alerts.py:136-146`): The kill switch monitoring thread calls `asyncio.get_event_loop().run_until_complete()` to dispatch alerts. On Python 3.12+, `asyncio.get_event_loop()` raises a `DeprecationWarning` and will raise a `RuntimeError` in a future release when called from a non-main thread with no running event loop. The call must be replaced with `asyncio.run()` to create a new event loop for each async invocation.

**Drawdown vs PnL peak** (`ops/kill_switch.py:353-374`): The kill switch calculates drawdown as a percentage of the PnL peak rather than the account balance. This produces a much smaller percentage during winning streaks (a 5% drawdown from a $1,000 PnL peak triggers at $50 loss, but the configured threshold may be meant relative to a $10,000 account). The calculation should be normalized against the initial account balance or the current NAV.

**Prometheus binding** (`ops/prometheus.py:215`): The Prometheus HTTP server binds to `0.0.0.0` by default, exposing metrics on all network interfaces. In a cloud environment this makes operational data (positions, PnL, margin ratio) accessible to any host on the same network. The default should be `127.0.0.1`.

---

## Area 8: FastAPI REST API Security

**Status**: GOOD with caveats

POST endpoints require an API key, rate limiting is implemented, and CORS is restricted to localhost origins. The API is reasonably secure for its intended local-control use case.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| POST endpoints blocked without API key (`require_auth=True`) | GOOD | Low | `ui/api.py:337-343` |
| Rate limiting implemented | GOOD | Low | `ui/api.py:151-171` |
| CORS restricted to localhost | GOOD | Low | `ui/api.py:248-258` |
| **Live mode can start without API key; POST returns 403 silently** | NEEDS IMPROVEMENT | High | `runners/run_live.py:44-48` |
| No runtime validation preventing `0.0.0.0` binding | NEEDS IMPROVEMENT | Medium | `ops/manager.py:85` |
| Exception detail leaks internals in 500 responses | NEEDS IMPROVEMENT | Low | `ui/api.py:401,457,481` |
| Swagger `/docs` unconditionally enabled | NEEDS IMPROVEMENT | Low | `ui/api.py:241-245` |

### Details

**Live mode without API key** (`runners/run_live.py:44-48`): The live runner starts successfully without an API key configured. The API server starts but all POST operations (flatten, set-throttle) immediately return 403. This means an operator who forgets to configure the key loses all runtime control without any startup warning.

**500 response leaks internals** (`ui/api.py:401,457,481`): Exception handlers return `str(e)` directly in the 500 response body. Stack traces and internal variable names should not be sent to clients; use a generic message and log the full exception server-side.

**Swagger unconditionally enabled** (`ui/api.py:241-245`): `/docs` and `/redoc` are always accessible. In production, interactive API documentation is an unnecessary attack surface and should be disabled unless explicitly opted in via configuration.

---

## Area 9: Security

**Status**: GOOD

No hardcoded API keys or secrets were found. YAML loading uses `safe_load` exclusively. SQLite queries use parameterized statements throughout.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| YAML uses `safe_load` exclusively | GOOD | Low | `utils/yamlio.py:85` |
| No hardcoded API keys anywhere in the codebase | GOOD | Low | All files |
| SQLite uses parameterized queries | GOOD | Low | `optimization/results_db.py:190-216` |
| **Unbounded dependency version ranges** | NEEDS IMPROVEMENT | Medium | `pyproject.toml:10-30` |
| **Telegram token exposed in DEBUG-logged URLs** | NEEDS IMPROVEMENT | Medium | `ops/alerts.py:293` |
| Slack webhook URL not validated before use | NEEDS IMPROVEMENT | Low | `ops/operations.py:157-160` |
| No testnet/live credential cross-validation | NEEDS IMPROVEMENT | Medium | `config/venue.py:20-27` |

### Details

**Telegram token in DEBUG logs** (`ops/alerts.py:293`): The full Telegram API URL including the bot token is logged at DEBUG level. If DEBUG logging is enabled (common during development), the bot token will appear in log files and any log aggregation service, where it can be used by anyone with log access to impersonate the bot.

**Unbounded dependencies** (`pyproject.toml:10-30`): Several key packages (NautilusTrader, pydantic, FastAPI) have no upper version bounds. A breaking change in any upstream library will silently propagate into the next install. Upper bounds should be pinned and updated deliberately.

**No credential cross-validation** (`config/venue.py:20-27`): Nothing prevents a testnet API key from being used with the production endpoint URL or vice versa. A validator on `VenueConfig` should check that testnet credentials are only paired with testnet base URLs (e.g., by checking for `testnet` in the URL when the key prefix indicates it is a testnet key).

---

## Area 10: Concurrency and Race Conditions

**Status**: NEEDS IMPROVEMENT

Nautilus single-thread assumptions are correctly scoped to the strategy internals. The parallel optimizer uses process spawning (not threads) which provides correct isolation. However, several cross-thread writes are unprotected.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Nautilus single-thread assumption correctly bounded | GOOD | Low | `strategy.py:2183-2211` |
| Parallel optimizer uses spawn isolation (correct) | GOOD | Low | `optimization/parallel_runner.py:162-165` |
| **`_throttle` written cross-thread without lock** | NEEDS IMPROVEMENT | Medium | `strategy.py:2231-2232` |
| **API callbacks in `ThreadPoolExecutor` cross-thread** | NEEDS IMPROVEMENT | Medium | `ui/api.py:282-306` |
| Metrics polling reads strategy state without coordination | NEEDS IMPROVEMENT | Low | `ops/manager.py:200-204` |
| Single transient metric can trigger kill switch | NEEDS IMPROVEMENT | Low | `ops/kill_switch.py:331-341` |
| `mp.set_start_method` called on every instantiation | NEEDS IMPROVEMENT | Low | `optimization/parallel_runner.py:162-165` |

### Details

**`_throttle` without lock** (`strategy.py:2231-2232`): The `set_throttle` API endpoint updates `self._throttle` from the FastAPI thread pool, while the strategy reads it in `on_bar()` on the Nautilus event loop thread. On CPython, assignment of a float is effectively atomic due to the GIL, but this is an implementation detail, not a guarantee. A `threading.Lock` or `threading.Event` should protect this write.

**API callbacks cross-thread** (`ui/api.py:282-306`): Several API endpoint handlers call strategy methods (e.g., `cancel_all_orders`) directly from the FastAPI `ThreadPoolExecutor`. These calls are not safe to invoke from outside the Nautilus event loop thread. They should be dispatched via `strategy.msgbus.send()` or an equivalent thread-safe mechanism provided by Nautilus.

**Single transient metric triggers kill switch** (`ops/kill_switch.py:331-341`): One anomalous reading of margin ratio or drawdown is sufficient to fire the kill switch. A 2-of-3 consecutive check requirement (hysteresis) would prevent a transient data error from triggering an irreversible position flatten.

---

## Area 11: Error Handling and Resilience

**Status**: NEEDS IMPROVEMENT

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Drawdown check has fail-safe defaults | GOOD | Low | `strategy.py:2694-2700` |
| Component isolation correct (`FundingGuard` failure non-fatal) | GOOD | Low | `strategy.py` |
| **`on_start()` config failure silent (no `_critical_error`)** | CRITICAL | Critical | `strategy.py:223-225` |
| **`_get_margin_ratio()` swallows exceptions silently** | NEEDS IMPROVEMENT | Medium | `strategy.py` |
| No state persistence (`_peak_balance`, `_realized_pnl` all in-memory) | NEEDS IMPROVEMENT | Medium | `strategy.py` |
| Kill switch verification uses blocking `time.sleep()` | NEEDS IMPROVEMENT | Medium | `ops/kill_switch.py:246-279` |

### Details

**`_get_margin_ratio()` silent swallow**: This method catches all exceptions and returns a safe default. While the fail-safe default is good, the exception is not logged, making it impossible to distinguish a transient data absence from a recurring failure.

**No state persistence**: `_peak_balance` and `_realized_pnl` are pure in-memory state. A process restart or node crash resets both to their initial values, meaning the drawdown protection and PnL tracking start over from zero. After a restart following a 4% drawdown, the strategy would not trigger the 5% drawdown limit until another 5% loss occurs. This is a meaningful gap for live trading.

---

## Area 12: Code Quality

**Status**: NEEDS IMPROVEMENT

The pure component layer (grid, policy, detector, funding guard, order sync) is well-documented with clear docstrings. The strategy integration layer carries significant complexity and several maintainability issues.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| `on_bar()` readability is good despite length | GOOD | Low | `strategy.py:735-949` |
| Pure component docstrings are excellent | GOOD | Low | `strategy/` |
| **`strategy.py` at 2,744 lines — needs decomposition** | NEEDS IMPROVEMENT | Medium | `strategies/hedge_grid_v1/strategy.py` |
| **Hardcoded `Decimal("0.01")` for TP/SL quantization** | NEEDS IMPROVEMENT | Medium | `strategy.py:1148,1153,684-701` |
| **Duplicated TP/SL price calculation logic** | NEEDS IMPROVEMENT | Medium | `strategy.py:1130-1162` vs `684-701` |
| 7+ `type: ignore` comments hiding real type issues | NEEDS IMPROVEMENT | Low | `strategy.py:1142,1237,1247` |
| Magic numbers for cache cleanup thresholds | NEEDS IMPROVEMENT | Low | `strategy.py:1445-1449,2089-2093` |
| Lazy imports inside methods (7 locations) | NEEDS IMPROVEMENT | Low | `strategy.py:228,248,327,1554,2356,2571,2680` |
| Stale docstring claims `reduce_only=True` but code sets `False` | NEEDS IMPROVEMENT | Low | `strategy.py:1879-1901` |
| `on_order_filled()` at 294 lines — needs decomposition | NEEDS IMPROVEMENT | Low | `strategy.py:996-1290` |
| `on_order_rejected()` at 217 lines, deeply nested | NEEDS IMPROVEMENT | Low | `strategy.py:1415-1632` |

### Details

**Strategy file size** (`strategy.py`): At 2,744 lines, this single file contains lifecycle methods, risk controls, order management, TP/SL attachment, hedge mode utilities, and diagnostic logging. It should be decomposed into mixins or helper classes: `RiskControlsMixin`, `OrderManagementMixin`, `HedgeModeUtils`, and `DiagnosticsLogger`.

**Hardcoded `Decimal("0.01")`** (`strategy.py:1148,1153,684-701`): TP and SL prices are rounded to 2 decimal places unconditionally. For instruments with a tick size of 0.1 or 0.5 (e.g., some futures), this produces invalid prices. The quantization should use `instrument.price_increment`.

**Duplicated TP/SL logic** (`strategy.py:1130-1162` vs `684-701`): The TP and SL price calculation appears in two separate places. Any fix or adjustment to the formula must be applied twice. This should be extracted into a private `_calculate_tp_sl_prices(fill_price, side, cfg)` method.

**Stale docstring** (`strategy.py:1879-1901`): The docstring for the TP/SL attachment method states orders are submitted as reduce-only, but the implementation passes `reduce_only=False`. This is an active confusion risk for maintainers.

---

## Area 13: Configuration Validation

**Status**: NEEDS IMPROVEMENT

Pydantic field constraints are thorough — ranges, enums, and cross-field validators are used appropriately. The YAML loader produces clear, actionable error messages on schema violations.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Pydantic field constraints thorough (`ge`/`le`/`gt`/enums) | GOOD | Low | `config/strategy.py:18-261` |
| `BaseYamlConfigLoader` produces clear error messages | GOOD | Low | `config/base.py:70-82` |
| **`risk_management: Optional` annotation is misleading (field has a default)** | NEEDS IMPROVEMENT | Medium | `config/strategy.py:282` |
| **No testnet/live URL cross-validation in `VenueConfig`** | NEEDS IMPROVEMENT | Critical | `config/venue.py:20-27` |
| No `start_time < end_time` validation in `BacktestConfig` | NEEDS IMPROVEMENT | Medium | `config/backtest.py:18-24` |
| Dead code validators (`validate_leverage`, `validate_drawdown`) | NEEDS IMPROVEMENT | Low | `config/strategy.py:187-194`, `ops/operations.py:84-91` |
| Dead code in `_suggest_float` in param space | NEEDS IMPROVEMENT | Low | `optimization/param_space.py:203` |
| Sharpe constraint allows `-999` (nonsensical minimum) | NEEDS IMPROVEMENT | Low | `optimization/constraints.py:44-46` |

### Details

**`Optional` annotation** (`config/strategy.py:282`): `risk_management` is typed as `Optional[RiskManagementConfig]` but always has a default instance. Code reading this type annotation would defensively guard against `None`, adding unnecessary branches. The annotation should be `RiskManagementConfig` with a default factory.

**No `start < end` validation** (`config/backtest.py:18-24`): A backtest config with `start_time >= end_time` passes validation and fails at runtime with an opaque engine error. A Pydantic `model_validator` should catch this condition at load time.

---

## Area 14: Testing Gaps

**Status**: CRITICAL

The pure component unit tests are excellent and well-structured. The operational controls test suite (kill switch, alerts, Prometheus) is thorough. The gap is entirely at the integration level.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Pure component unit test coverage excellent | GOOD | Low | `tests/strategy/` |
| API tests cover all endpoints | GOOD | Low | `tests/ops/test_api.py` |
| `getattr()` config bug regression test exists and passes | GOOD | Low | `tests/config/test_config_loading.py:383` |
| **ALL 27 strategy smoke tests permanently skipped** | CRITICAL | Critical | `tests/strategy/test_strategy_smoke.py` |
| **Parity and determinism tests permanently skipped** | CRITICAL | Critical | `tests/test_parity.py:573,639` |
| 5 ops integration tests skipped due to `_ops_lock` issue | NEEDS IMPROVEMENT | Medium | `tests/test_ops_integration.py` |
| No property-based tests for `GridEngine` or `OrderDiff` | NEEDS IMPROVEMENT | Medium | `tests/strategy/` |
| No test for circuit breaker cooldown/reset cycle | NEEDS IMPROVEMENT | Medium | `tests/ops/test_kill_switch.py` |
| Duplicate test files (macOS copies with spaces in names) | NEEDS IMPROVEMENT | Low | `tests/data/sources/` |

### Details

**27 permanently skipped smoke tests** (`tests/strategy/test_strategy_smoke.py`): Every test in this file is decorated with `@pytest.mark.skip`. The strategy `on_start()` → `on_bar()` → `on_order_filled()` → `on_stop()` lifecycle has no automated test coverage whatsoever. Critical bugs in `on_start()` (config failure handling, instrument not found), the risk gate bypass, and the TP/SL attachment logic all go undetected by CI. These must be rewritten using a `BacktestEngine` harness with minimal synthetic data.

**Parity and determinism tests** (`tests/test_parity.py:573,639`): The parity tests (verifying backtest vs paper trading produce identical results) and determinism tests (verifying two identical backtests produce identical results) are permanently skipped. These are high-value regression guards that should be operational.

**Duplicate test files**: macOS Finder has created space-renamed copies of two test files (`test_csv_source 2.py`, `test_websocket_source 2.py`). These shadow the originals and cause `pytest` to collect and run the same tests twice, inflating the reported pass count.

---

## Area 15: Data Pipeline

**Status**: NEEDS IMPROVEMENT

Deduplication logic is correct. However, the row-level Pydantic validation pattern is a significant performance problem for large datasets.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| Deduplication strategy correct | GOOD | Low | `data/pipelines/normalizer.py:84-87` |
| **Row-level Pydantic validation uses `iterrows()` (extremely slow)** | CRITICAL | High | `data/schemas.py:294-298` |
| Timestamp heuristic fragile (magnitude-based) | NEEDS IMPROVEMENT | Medium | `data/pipelines/normalizer.py:228-295` |
| No temporal gap detection in trade data | NEEDS IMPROVEMENT | Medium | `data/pipelines/normalizer.py:17-93` |
| `to_trade_tick` hardcodes 8 decimal precision | NEEDS IMPROVEMENT | Medium | `data/schemas.py:169-170` |
| `mark_prices_to_bars` hardcodes 2 decimal precision | NEEDS IMPROVEMENT | Medium | `data/schemas.py:367-373` |

### Details

**`iterrows()` validation** (`data/schemas.py:294-298`): Each row of a trade DataFrame is individually converted to a Pydantic model for validation. `iterrows()` is one of the slowest operations in pandas — for a million-row trade file, this loop can take 10+ minutes. Validation should be vectorized: apply type coercion to entire columns, then use a single bulk check for out-of-range values using pandas `Series` operations.

**Magnitude-based timestamp heuristic** (`data/pipelines/normalizer.py:228-295`): The normalizer infers the timestamp unit (seconds vs milliseconds vs microseconds vs nanoseconds) by checking the magnitude of the integer value. A timestamp of `1_700_000_000_000` could be either milliseconds since epoch (2023) or nanoseconds (January 1970 + 1.7 seconds). The heuristic can silently produce data shifted by three orders of magnitude.

**Hardcoded precision** (`data/schemas.py:169-170`, `367-373`): Trade tick price precision is hardcoded to 8 decimal places and mark price to 2 decimal places. When ingesting data for instruments with different tick sizes (e.g., ETH with 2-decimal precision), the hardcoded values produce prices that cannot be represented in the Nautilus type system.

---

## Area 16: Performance

**Status**: NEEDS IMPROVEMENT

`OrderDiff` caching and bounded cache cleanup are correctly implemented. At the current scale (10-20 grid levels per side), O(n) order cache scans are acceptable.

| Finding | Status | Risk | Location |
|---------|--------|------|----------|
| `OrderDiff` caching avoids redundant computation | GOOD | Low | `strategy/order_sync.py:198-203` |
| Key tracking dicts have bounded cleanup | GOOD | Low | `strategy.py:1444-1449` |
| **8+ INFO logs per bar in live mode** | NEEDS IMPROVEMENT | Medium | `strategy.py:808-940` |
| **`_retry_history` dict can grow unbounded** | NEEDS IMPROVEMENT | Medium | `strategy/order_sync.py:415` |
| SQLite3 datetime adapter deprecation (102 warnings per run) | NEEDS IMPROVEMENT | Low | `optimization/results_db.py:190` |
| O(n) order cache scan every bar (acceptable at current scale) | NEEDS IMPROVEMENT | Low | `strategy.py` hot path |

### Details

**8+ INFO logs per bar** (`strategy.py:808-940`): Each bar emits multiple `INFO`-level log lines for regime state, grid center, ladder depth, and order diff summary. At a 1-second bar frequency this generates over 28,000 log lines per hour. These should be downgraded to `DEBUG` level, with a single compact summary line remaining at `INFO`.

**`_retry_history` unbounded growth** (`strategy/order_sync.py:415`): The retry history dictionary in `OrderDiff` is appended to on every retry event and never pruned. Over a multi-day live trading session with periodic order rejections, this dict will grow to tens of thousands of entries. A bounded deque or time-based eviction should be used.

---

## Prioritized Action Plan

### Immediate Actions (under 1 hour each — blocks live trading)

These items are prerequisite to any live trading session:

1. **Change paper CLI default venue config** from `binance_futures.yaml` to `binance_futures_testnet.yaml` — `cli.py:243` (5 minutes)
2. **Set `_critical_error = True` and call `self.stop()` on `on_start()` config failure** — `strategy.py:223-225` (15 minutes)
3. **Set `_critical_error = True` on `on_start()` instrument not found** — `strategy.py:241-243` (10 minutes)
4. **Make kill switch startup failure fatal in live mode** — `ops/manager.py:146-148` (15 minutes)
5. **Fix `AlertManager`: replace `asyncio.get_event_loop().run_until_complete()` with `asyncio.run()`** — `ops/alerts.py:136-146` (30 minutes)
6. **Bind Prometheus server to `127.0.0.1` by default** — `ops/prometheus.py:215` (5 minutes)
7. **Add division-by-zero guard in diagnostic logging** — `strategy.py:880` (10 minutes)

### Short-Term Actions (1-2 days)

8. Add `_pause_trading` and `_circuit_breaker_active` checks to `_execute_add()` timer callback — `strategy.py:1613-1616` (15 minutes)
9. Rewrite `PrecisionGuard.clamp_price()` and `clamp_qty()` using `decimal.Decimal` — `exchange/precision.py:129,152` (1 hour)
10. Add testnet/live URL cross-validation `model_validator` in `VenueConfig` — `config/venue.py:20-27` (30 minutes)
11. Replace silent NETTING fallback with `ValueError` in both runners — `base_runner.py:473`, `run_backtest.py:301` (15 minutes)
12. Move `engine.dispose()` to after `_extract_results()` — `run_backtest.py:437-449` (5 minutes)
13. Delete duplicate macOS test files with spaces in names — `tests/data/sources/` (5 minutes)
14. Add `start_time < end_time` model validator in `BacktestConfig` — `config/backtest.py:18-24` (15 minutes)
15. Pin upper version bounds for NautilusTrader, pydantic, and FastAPI in `pyproject.toml` (30 minutes)
16. Set `reduce_only=True` on all TP, SL, and flatten orders — `strategy.py:1946,2047,2500` (30 minutes)
17. Redact Telegram bot token from DEBUG log URL — `ops/alerts.py:293` (10 minutes)

### Medium-Term Actions (1-2 weeks)

18. Rewrite 27 strategy smoke tests using `BacktestEngine` harness with synthetic OHLCV data
19. Rewrite parity and determinism tests against the current config schema — `tests/test_parity.py:573,639`
20. Decompose `strategy.py` into mixins: `RiskControlsMixin`, `OrderManagementMixin`, `HedgeModeUtils`, `DiagnosticsLogger`
21. Add property-based tests (Hypothesis) for `GridEngine.build_ladders()` and `OrderDiff.diff()`
22. Replace hardcoded `Decimal("0.01")` with `instrument.price_increment` — `strategy.py:1148,1153,684-701`
23. Add temporal gap detection in data normalizer — `data/pipelines/normalizer.py:17-93`
24. Replace `iterrows()` validation with vectorized pandas operations — `data/schemas.py:294-298`
25. Add 2-of-3 consecutive check hysteresis to kill switch circuit breaker — `ops/kill_switch.py:331-341`
26. Fix kill switch drawdown: calculate as percentage of account balance, not PnL peak — `ops/kill_switch.py:353-374`
27. Add `threading.Lock` guard on `_throttle` cross-thread write — `strategy.py:2231-2232`
28. Dispatch API-initiated strategy calls via `msgbus.send()` instead of direct method calls — `ui/api.py:282-306`
29. Disable Swagger `/docs` in production config — `ui/api.py:241-245`

### Long-Term Actions

30. Add lightweight state persistence for `_peak_balance` and `_realized_pnl` (SQLite or Redis)
31. Implement `on_reset()`, `on_dispose()`, and `on_degrade()` lifecycle methods — `strategy.py`
32. Add optional position closure logic on `on_stop()` — `strategy.py:564-592`
33. Move warmup HTTP calls to runner layer before the event loop starts — `strategy.py:364-506`, `binance_warmer.py:80`
34. Extract duplicated TP/SL price calculation into a shared private method — `strategy.py:1130-1162` vs `684-701`
35. Add `ops` warning or default-true for live mode `--enable-ops` — `cli.py:332-335`

---

## Finding Index by Severity

### Critical

| ID | Finding | Location |
|----|---------|----------|
| C1 | Paper CLI defaults to production venue config | `cli.py:243` |
| C2 | `on_start()` config failure does not set `_critical_error` | `strategy.py:223-225` |
| C3 | `on_start()` instrument not found returns without error flags | `strategy.py:241-243` |
| C4 | 27 strategy smoke tests permanently skipped | `tests/strategy/test_strategy_smoke.py` |
| C5 | Kill switch startup failure silently swallowed | `ops/manager.py:146-148` |
| C6 | `AlertManager` async broken on Python 3.12+ | `ops/alerts.py:136-146` |
| C7 | `PrecisionGuard` uses float arithmetic | `exchange/precision.py:129,152` |
| C8 | Division-by-zero in diagnostic logging | `strategy.py:880` |
| C9 | Parity and determinism tests permanently skipped | `tests/test_parity.py:573,639` |
| C10 | `iterrows()` row-level validation is extremely slow | `data/schemas.py:294-298` |

### High Risk

| ID | Finding | Location |
|----|---------|----------|
| H1 | `PaperRunner` with production config can place real orders | `base_runner.py:805-824` |
| H2 | Silent NETTING fallback when `hedge_mode=false` | `base_runner.py:473`, `run_backtest.py:301` |
| H3 | `BacktestRunner` disposes engine before extracting results | `run_backtest.py:437-449` |
| H4 | `AlertManager` alert delivery broken in kill switch thread | `ops/alerts.py:136-146` |
| H5 | Live mode API has no key configured; POST returns 403 silently | `runners/run_live.py:44-48` |

### Medium Risk

| ID | Finding | Location |
|----|---------|----------|
| M1 | Timer callback bypasses `_pause_trading` and `_circuit_breaker_active` gates | `strategy.py:1613-1616` |
| M2 | `reduce_only=False` on TP/SL/flatten orders | `strategy.py:1946,2047,2500` |
| M3 | Blocking HTTP warmup inside Nautilus event loop | `strategy.py:364-506`, `binance_warmer.py:80` |
| M4 | `on_order_filled()` re-raises exceptions | `strategy.py:1289-1290` |
| M5 | Grid center initialized to `0.0` — fragile on first bar | `strategy.py:101` |
| M6 | Kill switch drawdown calculated vs PnL peak, not account balance | `ops/kill_switch.py:353-374` |
| M7 | Prometheus binds to `0.0.0.0` | `ops/prometheus.py:215` |
| M8 | `_throttle` written cross-thread without lock | `strategy.py:2231-2232` |
| M9 | No state persistence for `_peak_balance` / `_realized_pnl` | `strategy.py` |
| M10 | Telegram token exposed in DEBUG-logged URLs | `ops/alerts.py:293` |
| M11 | No testnet/live URL cross-validation in `VenueConfig` | `config/venue.py:20-27` |
| M12 | `risk_management: Optional` annotation is misleading | `config/strategy.py:282` |
| M13 | No `start_time < end_time` validation in `BacktestConfig` | `config/backtest.py:18-24` |
| M14 | 8+ INFO logs per bar at 1-second frequency | `strategy.py:808-940` |
| M15 | `_retry_history` dict grows unbounded | `strategy/order_sync.py:415` |
| M16 | Hardcoded `Decimal("0.01")` TP/SL quantization | `strategy.py:1148,1153,684-701` |
| M17 | Timestamp heuristic fragile (magnitude-based) | `data/pipelines/normalizer.py:228-295` |
| M18 | No temporal gap detection in trade data | `data/pipelines/normalizer.py:17-93` |
| M19 | Hardcoded precision in `to_trade_tick` and `mark_prices_to_bars` | `data/schemas.py:169-170`, `367-373` |
| M20 | Single transient metric can trigger kill switch | `ops/kill_switch.py:331-341` |

---

*Report generated by 6 specialized audit agents examining all 63 source files and 42 test files.*
*Analysis covers areas: Architecture, Trading Logic, Hedge Mode, Risk Controls, NautilusTrader Lifecycle, Exchange Resilience, Operational Controls, API Security, Security, Concurrency, Error Handling, Code Quality, Configuration Validation, Testing, Data Pipeline, and Performance.*
