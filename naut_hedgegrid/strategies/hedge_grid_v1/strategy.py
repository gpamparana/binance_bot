"""HedgeGridV1 trading strategy implementation."""

import threading
from collections import deque
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    LiquiditySide,
)
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderCancelRejected,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderRejected,
)
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument  # noqa: TC002
from nautilus_trader.trading.strategy import Strategy

from naut_hedgegrid.config.strategy import HedgeGridConfig, HedgeGridConfigLoader
from naut_hedgegrid.domain.types import (
    Ladder,
    OrderIntent,
    Side,
)
from naut_hedgegrid.exchange.precision import PrecisionGuard
from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
from naut_hedgegrid.strategies.hedge_grid_v1.exit_manager import ExitManagerMixin
from naut_hedgegrid.strategies.hedge_grid_v1.metrics import MetricsMixin
from naut_hedgegrid.strategies.hedge_grid_v1.ops_api import OpsControlMixin
from naut_hedgegrid.strategies.hedge_grid_v1.order_events import OrderEventsMixin
from naut_hedgegrid.strategies.hedge_grid_v1.order_executor import OrderExecutionMixin
from naut_hedgegrid.strategies.hedge_grid_v1.risk_manager import RiskManagementMixin
from naut_hedgegrid.strategies.hedge_grid_v1.state_persistence import StatePersistenceMixin
from naut_hedgegrid.strategy.detector import Bar as DetectorBar, RegimeDetector
from naut_hedgegrid.strategy.funding_guard import FundingGuard
from naut_hedgegrid.strategy.grid import GridEngine
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff, PostOnlyRetryHandler
from naut_hedgegrid.strategy.policy import PlacementPolicy


class HedgeGridV1(
    RiskManagementMixin,
    MetricsMixin,
    OrderEventsMixin,
    OrderExecutionMixin,
    ExitManagerMixin,
    OpsControlMixin,
    StatePersistenceMixin,
    Strategy,
):
    """
    HedgeGridV1 futures trading strategy with adaptive grid and regime detection.

    This strategy orchestrates all hedge grid components to implement a dynamic
    grid trading system on futures markets with:

    - **Regime Detection**: Classifies market into UP/DOWN/SIDEWAYS using EMA/ADX/ATR
    - **Adaptive Grid**: Builds price ladders with regime-based directional bias
    - **Placement Policy**: Throttles counter-trend side based on regime
    - **Funding Guard**: Reduces exposure near funding time when costs are high
    - **Precision Guards**: Ensures all orders meet exchange requirements
    - **Order Synchronization**: Minimizes order churn via intelligent diffing

    Grid orders are placed as limit orders in Binance hedge mode (separate LONG/SHORT
    positions). When filled, take-profit and stop-loss orders are attached as
    reduce-only orders.

    Parameters
    ----------
    config : HedgeGridV1Config
        Strategy configuration with instrument, bar type, and config file path

    """

    def __init__(self, config: HedgeGridV1Config) -> None:
        """
        Initialize HedgeGridV1 strategy.

        Args:
            config: Strategy configuration

        """
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        # Note: bar_type is NOT parsed from string due to Nautilus 1.220.0 bug with PERP instruments
        # It will be constructed programmatically in on_start() after instrument is loaded
        self.bar_type: BarType | None = None
        self.config_path = config.hedge_grid_config_path

        # State tracking
        self._hedge_grid_config: HedgeGridConfig | None = None
        self._instrument: Instrument | None = None
        self._precision_guard: PrecisionGuard | None = None
        self._regime_detector: RegimeDetector | None = None
        self._funding_guard: FundingGuard | None = None
        self._order_diff: OrderDiff | None = None
        self._retry_handler: PostOnlyRetryHandler | None = None
        self._pending_retries: dict[str, OrderIntent] = {}
        self._last_mid: float | None = None
        self._grid_center: float = 0.0

        # Strategy identifier for order IDs
        self._strategy_name = "HG1"

        # Venue for order queries
        self._venue = Venue("BINANCE")

        # Operational controls state
        self._kill_switch = None
        self._throttle: float = 1.0  # Default to full aggressiveness

        # Metrics tracking
        self._start_time: int | None = None
        self._last_bar_time: datetime | None = None
        self._total_fills: int = 0
        self._maker_fills: int = 0

        # Order ID uniqueness counter (ensures no duplicate IDs)
        self._order_id_counter: int = 0
        self._order_id_lock = threading.Lock()  # Thread safety for counter

        # Track fills to prevent duplicate TP/SL creation
        self._fills_with_exits: set[str] = set()
        self._fills_lock = threading.Lock()  # Thread safety for fills tracking

        # Track TP/SL pairs for OCO-like cancellation
        # Maps fill_key (e.g., "LONG-5") -> (tp_client_order_id, sl_client_order_id)
        self._tp_sl_pairs: dict[str, tuple[str, str]] = {}
        self._tp_sl_pairs_lock = threading.Lock()

        # Ladder state for snapshot access
        self._last_long_ladder: Ladder | None = None
        self._last_short_ladder: Ladder | None = None

        # =====================================================================
        # RISK MANAGEMENT COMPONENTS
        # =====================================================================

        # Circuit breaker for error monitoring
        self._error_window: deque = deque(maxlen=100)  # Track last 100 errors with timestamps
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_reset_time: float | None = None

        # Drawdown protection
        self._peak_balance: float = 0.0
        self._initial_balance: float | None = None
        self._drawdown_protection_triggered: bool = False

        # Position validation & configurable timing (defaults overridden in on_start)
        self._last_balance_check: float = 0.0
        self._balance_check_interval: int = 60_000_000_000
        self._tp_sl_buffer_mult: float = 0.0005  # 5 bps default
        self._circuit_breaker_window_ns: int = 60_000_000_000
        self._ladder_lock = threading.Lock()  # Thread safety for ladder snapshots
        self._ops_lock = threading.Lock()  # Thread safety for API→strategy mutations (flatten, throttle)

        # Error recovery state
        self._critical_error = False  # Flag to indicate critical error state
        self._pause_trading = False  # Flag to pause trading after critical error

        # Performance optimization: internal grid order tracking (O(1) lookups)
        self._grid_orders_cache: dict[str, LiveOrder] = {}  # Track grid orders by client_order_id
        self._grid_orders_lock = threading.Lock()  # Thread safety for grid order cache

        # Rejection tracking for idempotency (Phase 2.3 fix: initialize in __init__)
        self._processed_rejections: set[str] = set()
        self._rejections_lock = threading.Lock()  # Thread safety for rejection tracking

        # Position retry tracking for cache lag handling (Phase 2.2 fix)
        self._position_retry_counts: dict[str, int] = {}

        # Instance-level order ID parsing cache (Phase 4.4 fix: avoid lru_cache class collision)
        self._parsed_order_id_cache: dict[str, dict] = {}

        # Diagnostic logging throttle (initialize to 0 instead of using hasattr)
        self._last_diagnostic_log: int = 0

        # Funding rate tracking (fed from mark price stream in live/paper trading)
        self._last_funding_rate: float = 0.0

        # Realized PnL tracking (accumulated from TP/SL fill events)
        self._realized_pnl: float = 0.0

        # On-start position reconciliation flag
        self._positions_reconciled: bool = False

        # Optimization mode flag (set properly in on_start after config load)
        self._is_optimization_mode: bool = False

        # Backtest mode flag (set in on_start based on clock type)
        self._is_backtest_mode: bool = False

    def on_start(self) -> None:
        """
        Start the strategy.

        Lifecycle:
        1. Load HedgeGridConfig from YAML
        2. Get instrument and create PrecisionGuard
        3. Initialize all strategy components
        4. Subscribe to bar data
        5. Log initialization complete

        """
        self.log.info("Starting HedgeGridV1 strategy")

        # Load hedge grid configuration
        try:
            self._hedge_grid_config = HedgeGridConfigLoader.load(self.config_path)
            # Cache optimization mode flag for fast checks
            self._is_optimization_mode = self._hedge_grid_config.execution.optimization_mode
            # Apply configurable timing constants
            self._balance_check_interval = (
                self._hedge_grid_config.execution.balance_check_interval_seconds * 1_000_000_000
            )
            self._tp_sl_buffer_mult = self._hedge_grid_config.execution.tp_sl_adjustment_buffer_bps / 10_000
            rm_cfg = self._hedge_grid_config.risk_management
            self._circuit_breaker_window_ns = (
                rm_cfg.circuit_breaker_window_seconds * 1_000_000_000 if rm_cfg else 60_000_000_000
            )
            if not self._is_optimization_mode:
                self.log.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            self.log.error(f"Failed to load config from {self.config_path}: {e}")
            self._critical_error = True
            self._pause_trading = True
            return

        # Defense-in-depth: verify hedge mode is enabled
        from nautilus_trader.model.enums import OmsType

        if self.config.oms_type != OmsType.HEDGING:
            self.log.error(
                f"HedgeGridV1 requires OmsType.HEDGING but got {self.config.oms_type}. "
                "Set hedge_mode: true in venue config."
            )
            self._critical_error = True
            self._pause_trading = True
            return

        # Get instrument from cache
        self._instrument = self.cache.instrument(self.instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found in cache")
            self._critical_error = True
            self._pause_trading = True
            return

        self.log.info(f"Trading instrument: {self._instrument.id}")

        # Construct BarType programmatically to avoid Nautilus 1.220.0 parsing bug
        from nautilus_trader.model.data import BarSpecification
        from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType

        bar_spec = BarSpecification(
            step=1,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.LAST,
        )
        self.bar_type = BarType(
            instrument_id=self.instrument_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )
        self.log.info(f"Bar type: {self.bar_type}")

        # Create precision guard
        self._precision_guard = PrecisionGuard(instrument=self._instrument)
        self.log.info(
            f"Precision guard initialized: "
            f"tick={self._precision_guard.precision.price_tick}, "
            f"step={self._precision_guard.precision.qty_step}, "
            f"min_notional={self._precision_guard.precision.min_notional}"
        )

        # Initialize retry handler for post-only rejections
        self._retry_handler = PostOnlyRetryHandler(
            precision_guard=self._precision_guard,
            max_attempts=self._hedge_grid_config.execution.retry_attempts,
            enabled=self._hedge_grid_config.execution.use_post_only_retries,
        )
        self.log.info(
            f"Retry handler initialized: "
            f"enabled={self._retry_handler.enabled}, "
            f"max_attempts={self._hedge_grid_config.execution.retry_attempts}"
        )

        # Initialize regime detector
        regime_cfg = self._hedge_grid_config.regime
        self._regime_detector = RegimeDetector(
            ema_fast=regime_cfg.ema_fast,
            ema_slow=regime_cfg.ema_slow,
            adx_len=regime_cfg.adx_len,
            atr_len=regime_cfg.atr_len,
            hysteresis_bps=regime_cfg.hysteresis_bps,
        )
        self.log.info(
            f"Regime detector initialized: "
            f"EMA({regime_cfg.ema_fast}/{regime_cfg.ema_slow}), "
            f"ADX({regime_cfg.adx_len}), "
            f"ATR({regime_cfg.atr_len})"
        )

        # Initialize funding guard
        funding_cfg = self._hedge_grid_config.funding
        self._funding_guard = FundingGuard(
            window_minutes=funding_cfg.funding_window_minutes,
            max_cost_bps=funding_cfg.funding_max_cost_bps,
        )
        self.log.info(
            f"Funding guard initialized: "
            f"window={funding_cfg.funding_window_minutes}min, "
            f"max_cost={funding_cfg.funding_max_cost_bps}bps"
        )

        # Initialize order diff engine
        self._order_diff = OrderDiff(
            strategy_name=self._strategy_name,
            precision_guard=self._precision_guard,
            price_tolerance_bps=self._hedge_grid_config.execution.order_diff_price_tolerance_bps,
            qty_tolerance_pct=self._hedge_grid_config.execution.order_diff_qty_tolerance_pct,
        )
        self.log.info("Order diff engine initialized")

        # Subscribe to bars
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to bars: {self.bar_type}")

        # Subscribe to mark price updates for funding rate data (live/paper only)
        try:
            from nautilus_trader.adapters.binance.futures.types import BinanceFuturesMarkPriceUpdate
            from nautilus_trader.model.data import DataType

            mark_price_type = DataType(
                BinanceFuturesMarkPriceUpdate,
                metadata={"instrument_id": self.instrument_id},
            )
            self.subscribe_data(mark_price_type, instrument_id=self.instrument_id)
            self.log.info("Subscribed to mark price updates for funding rate data")
        except ImportError:
            self.log.debug("BinanceFuturesMarkPriceUpdate not available (backtest mode), funding guard passive")
        except Exception as e:
            self.log.warning(f"Could not subscribe to mark price data: {e}")

        # Initialize metrics tracking
        self._start_time = self.clock.timestamp_ns()
        self._last_bar_time = None
        self._total_fills = 0
        self._maker_fills = 0
        self.log.info("Metrics tracking initialized")

        # Risk management is already initialized in __init__
        # Risk checks are called during trading: drawdown in on_bar,
        # circuit breaker in on_order_rejected/denied, validation in _execute_add

        # Detect backtest mode based on clock type
        self._is_backtest_mode = "Test" in self.clock.__class__.__name__

        # Hydrate grid orders cache from exchange (prevents duplicate orders on restart)
        self._hydrate_grid_orders_cache()

        # Load persisted state (peak_balance, realized_pnl) for live/paper modes
        self._load_persisted_state()

        # Consume prefetched warmup bars (set by runner layer before node.run())
        prefetched = getattr(self, "_prefetched_warmup_bars", None)
        if prefetched:
            self.warmup_regime_detector(prefetched)
            del self._prefetched_warmup_bars  # Free memory
        elif not self._is_backtest_mode:
            self.log.warning(
                "No prefetched warmup bars available. Regime detector will warm up "
                "from live bars (~50 bars). Use BaseRunner to provide warmup."
            )

        self.log.info("HedgeGridV1 strategy started successfully")

    def warmup_regime_detector(self, historical_bars: list[DetectorBar]) -> None:
        """
        Warm up the regime detector with historical bar data.

        This method should be called before live trading starts to ensure the
        regime detector has sufficient data to make reliable classifications.

        Parameters
        ----------
        historical_bars : list[DetectorBar]
            Historical bars ordered from oldest to newest
        """
        if self._regime_detector is None:
            self.log.error("Cannot warmup: regime detector not initialized")
            return

        if not historical_bars:
            self.log.warning("No historical bars provided for warmup")
            return

        self.log.info(f"Warming up regime detector with {len(historical_bars)} historical bars")

        # Feed each bar to the regime detector
        for i, bar in enumerate(historical_bars):
            self._regime_detector.update_from_bar(bar)

            # Log progress periodically
            if (i + 1) % 10 == 0:
                regime = self._regime_detector.current()
                is_warm = self._regime_detector.is_warm
                self.log.debug(f"Warmup progress: {i + 1}/{len(historical_bars)} bars, regime={regime}, warm={is_warm}")

        # Final status
        final_regime = self._regime_detector.current()
        is_warm = self._regime_detector.is_warm

        if is_warm:
            try:
                ema_fast_val = self._regime_detector.ema_fast.value if self._regime_detector.ema_fast.value else 0
                ema_slow_val = self._regime_detector.ema_slow.value if self._regime_detector.ema_slow.value else 0
                adx_val = self._regime_detector.adx.value if self._regime_detector.adx.value else 0
                self.log.info(
                    f"✓ Regime detector warmup complete: current regime={final_regime}, "
                    f"EMA fast={ema_fast_val:.2f}, "
                    f"EMA slow={ema_slow_val:.2f}, "
                    f"ADX={adx_val:.2f}"
                )
            except Exception:
                # Simpler logging if there's an issue
                self.log.info(f"✓ Regime detector warmup complete: current regime={final_regime}")
        else:
            self.log.warning(
                f"⚠ Regime detector still not warm after {len(historical_bars)} bars. "
                f"More historical data may be needed."
            )

    def on_stop(self) -> None:
        """
        Stop the strategy.

        Performs clean shutdown:
        1. Cancel all open grid orders
        2. Log final state
        3. Reset internal state

        Note: Positions are NOT automatically closed. Manual intervention
        required if positions should be closed on strategy stop.

        """
        self.log.info("Stopping HedgeGridV1 strategy")

        # Cancel all open grid orders (optimized with cache query)
        open_orders = [
            order
            for order in self.cache.orders_open(venue=self._venue)
            if order.client_order_id.value.startswith(self._strategy_name)
        ]

        if open_orders:
            self.log.info(f"Canceling {len(open_orders)} open orders")
            for order in open_orders:
                if order.is_open:
                    self.cancel_order(order)

        # Persist state before shutdown
        self._save_persisted_state()

        self.log.info("HedgeGridV1 strategy stopped")

    def on_reset(self) -> None:
        """
        Reset the strategy to initial state.

        Called by the optimization framework when reusing a strategy instance
        between trials. All mutable state must be cleared.
        """
        self.log.info("Resetting HedgeGridV1 strategy")

        # Configuration (reloaded in on_start)
        self._hedge_grid_config = None
        self._instrument = None
        self.bar_type = None

        # Components (recreated in on_start)
        self._precision_guard = None
        self._regime_detector = None
        self._funding_guard = None
        self._order_diff = None
        self._retry_handler = None

        # Tracking state
        self._last_mid = None
        self._grid_center = 0.0
        self._last_long_ladder = None
        self._last_short_ladder = None
        self._last_funding_rate = 0.0
        self._total_fills = 0
        self._maker_fills = 0
        self._start_time = None
        self._last_bar_time = None
        self._order_id_counter = 0
        self._last_diagnostic_log = 0

        # PnL & risk state
        self._peak_balance = 0.0
        self._initial_balance = None
        self._realized_pnl = 0.0
        self._drawdown_protection_triggered = False
        self._circuit_breaker_active = False
        self._circuit_breaker_reset_time = None
        self._error_window.clear()
        self._pause_trading = False
        self._critical_error = False
        self._throttle = 1.0
        self._last_balance_check = 0.0

        # Order caches
        self._grid_orders_cache.clear()
        self._fills_with_exits.clear()
        self._tp_sl_pairs.clear()
        self._pending_retries.clear()
        self._processed_rejections.clear()
        self._position_retry_counts.clear()
        self._parsed_order_id_cache.clear()
        self._positions_reconciled = False

        # Mode flags (reset to defaults, set properly in on_start)
        self._is_optimization_mode = False
        self._is_backtest_mode = False

        self.log.info("HedgeGridV1 strategy reset complete")

    def on_dispose(self) -> None:
        """
        Dispose the strategy (final cleanup before destruction).

        Performs a final state save for live/paper modes.
        """
        self._save_persisted_state()
        self.log.info("HedgeGridV1 strategy disposed")

    def on_degrade(self) -> None:
        """
        Handle degraded state (e.g., data feed issues).

        Pauses trading to prevent placing orders on stale data.
        """
        self._pause_trading = True
        self.log.warning("HedgeGridV1 strategy degraded — trading paused")

    def _hydrate_grid_orders_cache(self) -> None:
        """Hydrate grid order cache from Nautilus cache after exchange reconciliation.

        On restart, Nautilus reconciles open orders from the exchange. This method
        reads those orders and populates _grid_orders_cache to prevent the first
        on_bar() from placing duplicate grid orders.
        """
        try:
            open_orders = self.cache.orders_open(venue=self._venue)
        except Exception as e:
            self.log.warning(f"Could not query open orders for cache hydration: {e}")
            return

        hydrated = 0
        for order in open_orders:
            client_order_id = str(order.client_order_id.value)

            # Only track our grid orders (skip TP/SL and non-strategy orders)
            if not client_order_id.startswith(self._strategy_name):
                continue
            if "-TP-" in client_order_id or "-SL-" in client_order_id:
                continue

            try:
                parsed = self._parse_cached_order_id(client_order_id)
                live_order = LiveOrder(
                    client_order_id=client_order_id,
                    side=parsed["side"],
                    price=float(order.price) if hasattr(order, "price") else 0.0,
                    qty=float(order.quantity),
                    status="OPEN",
                )
                with self._grid_orders_lock:
                    self._grid_orders_cache[client_order_id] = live_order
                hydrated += 1
            except (ValueError, KeyError) as e:
                self.log.warning(f"Could not hydrate order {client_order_id}: {e}")

        if hydrated > 0:
            self.log.info(f"Hydrated {hydrated} existing grid orders from exchange cache")

    def _reconcile_existing_positions(self, mid: float) -> None:
        """Attach TP/SL to positions that existed before this session started.

        On restart, NautilusTrader reconciles positions from the exchange but
        TP/SL exit orders from the previous session are lost. This method
        detects unprotected positions and attaches new exit orders.

        Called once on the first trading bar after startup.
        """
        self._positions_reconciled = True

        if self._hedge_grid_config is None or self._instrument is None:
            self.log.warning("[RECONCILE] Config or instrument not loaded, skipping")
            return

        for side in (Side.LONG, Side.SHORT):
            position_id = PositionId(f"{self.instrument_id}-{side.value}")
            position = self.cache.position(position_id)

            if position is None or position.quantity == 0:
                continue

            # Check existing TP/SL coverage by summing quantities
            tp_qty_total = 0.0
            sl_qty_total = 0.0
            side_abbr = "L" if side == Side.LONG else "S"
            for order in self.cache.orders_open(venue=self._venue):
                oid = order.client_order_id.value
                if f"-TP-{side_abbr}" in oid:
                    tp_qty_total += float(order.quantity)
                if f"-SL-{side_abbr}" in oid:
                    sl_qty_total += float(order.quantity)

            qty = float(position.quantity)
            min_coverage = min(tp_qty_total, sl_qty_total)

            if min_coverage >= qty * 0.95:  # 5% tolerance for rounding
                self.log.info(
                    f"[RECONCILE] {side} position ({qty}) fully covered by "
                    f"existing TP({tp_qty_total:.6f})/SL({sl_qty_total:.6f}), skipping"
                )
                continue

            # Calculate gap quantity that needs TP/SL coverage
            gap_qty = qty - min_coverage

            # Calculate TP/SL from average entry price
            entry_price = float(position.avg_px_open)

            mid_decimal = Decimal(str(mid))
            entry_decimal = Decimal(str(entry_price))
            step_bps = Decimal(str(self._hedge_grid_config.grid.grid_step_bps))
            price_step = mid_decimal * (step_bps / Decimal("10000"))

            if side == Side.LONG:
                tp_price = float(
                    (entry_decimal + Decimal(self._hedge_grid_config.exit.tp_steps) * price_step).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
                sl_price = float(
                    (entry_decimal - Decimal(self._hedge_grid_config.exit.sl_steps) * price_step).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
            else:
                tp_price = float(
                    (entry_decimal - Decimal(self._hedge_grid_config.exit.tp_steps) * price_step).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
                sl_price = float(
                    (entry_decimal + Decimal(self._hedge_grid_config.exit.sl_steps) * price_step).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )

            ts_now = self.clock.timestamp_ns()

            self.log.info(
                f"[RECONCILE] Attaching TP/SL to existing {side} position: "
                f"pos_qty={qty}, gap_qty={gap_qty:.6f}, existing_coverage={min_coverage:.6f}, "
                f"entry={entry_price:.2f}, TP={tp_price:.2f}, SL={sl_price:.2f}"
            )

            tp_order = self._create_tp_order(
                side=side,
                quantity=gap_qty,
                tp_price=tp_price,
                level=0,
                fill_event_ts=ts_now,
            )
            sl_order = self._create_sl_order(
                side=side,
                quantity=gap_qty,
                sl_price=sl_price,
                level=0,
                fill_event_ts=ts_now,
            )

            self.submit_order(tp_order, position_id=position_id)
            self.submit_order(sl_order, position_id=position_id)

            # Register TP/SL pair so OCO cancellation works when one side fills.
            # Uses level=0 fill_key format consistent with _cancel_counterpart_exit lookup.
            fill_key = f"{side.value}-0"
            tp_order_id = tp_order.client_order_id.value
            sl_order_id = sl_order.client_order_id.value
            with self._tp_sl_pairs_lock:
                self._tp_sl_pairs[fill_key] = (tp_order_id, sl_order_id)

            # Mark as having exits so on_order_filled doesn't duplicate
            with self._fills_lock:
                self._fills_with_exits.add(fill_key)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle new bar data.

        Main strategy logic:
        1. Calculate mid price from bar close
        2. Update regime detector with bar data
        3. Check if grid recentering needed
        4. Build ladders using GridEngine
        5. Apply PlacementPolicy for regime-based throttling
        6. Apply FundingGuard for funding cost management
        7. Generate order diff vs live orders
        8. Execute diff operations (cancels, replaces, adds)

        Args:
            bar: New bar data

        """
        # SAFETY CHECK: Skip all trading if in critical error or paused state
        if self._critical_error or self._pause_trading:
            return

        # RISK GATE: Drawdown check runs UNCONDITIONALLY before anything else
        # (even during warmup) to catch pre-existing drawdown at startup
        if self._hedge_grid_config is not None:
            self._check_drawdown_limit()
            if self._pause_trading:
                return

        # Check all components are initialized before processing bar
        if (
            self._hedge_grid_config is None
            or self._regime_detector is None
            or self._funding_guard is None
            or self._order_diff is None
            or self._precision_guard is None
            or self._instrument is None
        ):
            self.log.warning("Strategy not fully initialized, skipping bar")
            return

        # Update last bar timestamp for metrics
        self._last_bar_time = datetime.fromtimestamp(bar.ts_init / 1_000_000_000, tz=UTC)

        # Stale data guard: skip order placement if bar data is too old (live/paper only)
        if not self._is_backtest_mode and self._hedge_grid_config.execution.max_bar_staleness_seconds > 0:
            bar_age_seconds = (datetime.now(tz=UTC) - self._last_bar_time).total_seconds()
            max_staleness = self._hedge_grid_config.execution.max_bar_staleness_seconds
            if bar_age_seconds > max_staleness:
                if not self._is_optimization_mode:
                    self.log.warning(
                        f"Bar data stale: age={bar_age_seconds:.0f}s > threshold={max_staleness}s. "
                        f"Skipping order placement."
                    )
                return

        # Calculate mid price (using close as proxy)
        mid = float(bar.close)
        self._last_mid = mid

        # Convert Nautilus Bar to detector Bar
        detector_bar = DetectorBar(
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

        # Update regime detector
        self._regime_detector.update_from_bar(detector_bar)
        regime = self._regime_detector.current()

        # Log regime and price with error handling (skip in optimization mode)
        if not self._is_optimization_mode:
            try:
                warm_status = self._regime_detector.is_warm
                self.log.info(f"Bar: close={mid:.2f}, regime={regime}, warm={warm_status}")
            except Exception as e:
                # Fallback logging if there's an issue with property access
                self.log.warning(f"Error logging bar info: {e}. Bar close={mid:.2f}")

        # Check if detector is warm enough for trading
        if not self._regime_detector.is_warm:
            if not self._is_optimization_mode:
                self.log.info("Regime detector not warm yet, skipping trading")
            return

        if self._circuit_breaker_active:
            # Check if cooldown has expired (resets inside _check_circuit_breaker)
            self._check_circuit_breaker()
            if self._circuit_breaker_active:
                if not self._is_optimization_mode:
                    self.log.debug("Circuit breaker active, skipping bar")
                return

        # One-time: reconcile pre-existing positions from previous session
        if not self._positions_reconciled:
            self._reconcile_existing_positions(mid)

        # Check if grid recentering needed
        recenter_needed = GridEngine.recenter_needed(
            mid=mid,
            last_center=self._grid_center,
            cfg=self._hedge_grid_config,
        )

        if recenter_needed:
            self.log.info(f"Grid recentering triggered at mid={mid:.2f}")
            self._grid_center = mid

            # Cancel ALL orphaned TP/SL orders before clearing tracking.
            # Old TP/SL orders are at stale prices relative to the new grid center.
            cancelled_exits = self._cancel_all_exit_orders()

            # Clear fill tracking so new grid levels can get TP/SL orders.
            old_count = len(self._fills_with_exits)
            with self._fills_lock:
                self._fills_with_exits.clear()

            # Clear TP/SL pair tracking
            with self._tp_sl_pairs_lock:
                self._tp_sl_pairs.clear()

            if old_count > 0 or cancelled_exits > 0:
                self.log.info(
                    f"[RECENTER] Cleared {old_count} stale fill entries, "
                    f"cancelled {cancelled_exits} orphaned TP/SL orders"
                )

            # Immediately re-attach TP/SL to surviving positions (no one-bar gap).
            # The quantity-aware reconcile will create TP/SL for the full position
            # since we just cleared all exits above.
            self._reconcile_existing_positions(mid)

        # Build ladders
        ladders = GridEngine.build_ladders(
            mid=self._grid_center,  # Use stable grid center, not current price
            cfg=self._hedge_grid_config,
            regime=regime,
        )

        self.log.debug(
            f"Built {len(ladders)} ladder(s): " + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Log grid center vs current price for debugging
        if self._grid_center > 0:
            deviation_bps = abs(mid - self._grid_center) / self._grid_center * 10000
            self.log.debug(
                f"Grid center: {self._grid_center:.2f}, Current mid: {mid:.2f}, Deviation: {deviation_bps:.1f} bps"
            )

        # Store ladder state for snapshot access (before any filtering)
        # Note: Python reference assignment is atomic, no lock needed
        # Update ladder state with thread safety
        with self._ladder_lock:
            for ladder in ladders:
                if ladder.side == Side.LONG:
                    self._last_long_ladder = ladder
                elif ladder.side == Side.SHORT:
                    self._last_short_ladder = ladder

        # Apply placement policy
        ladders = PlacementPolicy.shape_ladders(
            ladders=ladders,
            regime=regime,
            cfg=self._hedge_grid_config,
        )

        self.log.debug(
            f"After policy: {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Apply funding guard
        now = datetime.now(tz=UTC)
        ladders = self._funding_guard.adjust_ladders(ladders=ladders, now=now)

        if not self._is_optimization_mode:
            self.log.debug(
                f"After funding: {len(ladders)} ladder(s): "
                + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
            )

        # Apply throttle factor to ladder quantities (from ops API)
        if self._throttle < 1.0:
            ladders = [self._apply_throttle(ladder) for ladder in ladders]
            if not self._is_optimization_mode:
                self.log.debug(
                    f"After throttle ({self._throttle:.2f}): "
                    + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
                )

        # Filter rungs that would cross the spread at current market price
        ladders = [ladder.filter_placeable(mid) for ladder in ladders]

        # Generate diff (using cache query instead of manual tracking)
        live_orders_list = self._get_live_grid_orders()
        diff_result = self._order_diff.diff(
            desired_ladders=ladders,
            live_orders=live_orders_list,
        )

        self.log.info(
            f"Diff result: {len(diff_result.adds)} adds, "
            f"{len(diff_result.cancels)} cancels, "
            f"{len(diff_result.replaces)} replaces"
        )

        # Execute diff operations
        self._execute_diff(diff_result)

        # Diagnostic logging for fill monitoring (every 5 minutes)
        current_time = self.clock.timestamp_ns()
        if current_time - self._last_diagnostic_log >= 300_000_000_000:
            self._last_diagnostic_log = current_time
            self._log_diagnostic_status()

    def on_event(self, event: Event) -> None:
        """
        Handle generic events.

        Routes events to specific handlers based on type.

        Args:
            event: Event to process

        """
        if isinstance(event, OrderFilled):
            self.on_order_filled(event)
        elif isinstance(event, OrderAccepted):
            self.on_order_accepted(event)
        elif isinstance(event, OrderCanceled):
            self.on_order_canceled(event)
        elif isinstance(event, OrderRejected):
            self.on_order_rejected(event)
        elif isinstance(event, OrderDenied):
            self.on_order_denied(event)
        elif isinstance(event, OrderExpired):
            self._on_order_expired(event)
        elif isinstance(event, OrderCancelRejected):
            self._on_order_cancel_rejected(event)

    def on_data(self, data) -> None:
        """Handle custom data events (mark price updates with funding rate)."""
        try:
            from nautilus_trader.adapters.binance.futures.types import BinanceFuturesMarkPriceUpdate

            if isinstance(data, BinanceFuturesMarkPriceUpdate):
                funding_rate = float(data.funding_rate)
                self._last_funding_rate = funding_rate

                if self._funding_guard is not None and hasattr(data, "next_funding_time"):
                    self._funding_guard.on_funding_update(
                        rate=funding_rate,
                        next_ts=data.next_funding_time,
                    )
        except ImportError:
            pass
        except Exception as e:
            if not self._is_optimization_mode:
                self.log.debug(f"Error processing mark price data: {e}")

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle order filled event with comprehensive error recovery.

        When a grid order fills:
        1. Parse client_order_id to get original rung metadata
        2. Retrieve rung TP/SL prices from grid calculation
        3. Create TP limit order at tp_price (reduce_only=False, see order_executor.py NOTE)
        4. Create SL stop-market order at sl_price (reduce_only=False, see order_executor.py NOTE)
        5. Submit both with correct position_id suffix

        Args:
            event: Order filled event

        """
        try:
            # Check if we're in critical error state
            if self._critical_error:
                self.log.warning(f"Strategy in critical error state, ignoring fill event: {event.client_order_id}")
                return

            self.log.info(f"[FILL EVENT] Order filled: {event.client_order_id} @ {event.last_px}, qty={event.last_qty}")

            client_order_id = str(event.client_order_id.value)

            # Clean up retry tracking (order filled successfully)
            if client_order_id in self._pending_retries:
                del self._pending_retries[client_order_id]
                if self._retry_handler is not None:
                    self._retry_handler.clear_history(client_order_id)

            # Track fill statistics for metrics (atomic integer increment)
            self._total_fills += 1
            if event.liquidity_side == LiquiditySide.MAKER:
                self._maker_fills += 1

            # Remove filled grid order from internal cache
            if "-TP-" not in client_order_id and "-SL-" not in client_order_id:
                with self._grid_orders_lock:
                    self._grid_orders_cache.pop(client_order_id, None)

            # Get the filled order
            order = self.cache.order(event.client_order_id)
            if order is None:
                self.log.warning(f"Could not find order {event.client_order_id} in cache")
                return

            # Only handle grid orders (not TP/SL orders)
            if not event.client_order_id.value.startswith(self._strategy_name):
                return

            # TP/SL fills are exit order completions -- no further TP/SL needed
            if "-TP-" in client_order_id or "-SL-" in client_order_id:
                order_type = "TP" if "-TP-" in client_order_id else "SL"
                self.log.info(
                    f"[{order_type} FILLED] Exit order filled: {event.client_order_id} "
                    f"@ {event.last_px}, qty={event.last_qty}"
                )

                # Track realized PnL from exit fills
                try:
                    fill_price = float(event.last_px)
                    fill_qty = float(event.last_qty)
                    parts = client_order_id.split("-")
                    if len(parts) >= 3:
                        side_abbr = parts[2][0] if len(parts[2]) >= 1 else None
                        if side_abbr == "L":
                            # Closing a LONG position: PnL = (sell_price - entry_price) * qty
                            pos = self.cache.position(PositionId(f"{self.instrument_id}-LONG"))
                            if pos and hasattr(pos, "avg_px_open"):
                                self._realized_pnl += (fill_price - float(pos.avg_px_open)) * fill_qty
                        elif side_abbr == "S":
                            # Closing a SHORT position: PnL = (entry_price - buy_price) * qty
                            pos = self.cache.position(PositionId(f"{self.instrument_id}-SHORT"))
                            if pos and hasattr(pos, "avg_px_open"):
                                self._realized_pnl += (float(pos.avg_px_open) - fill_price) * fill_qty
                    # Save state after PnL update
                    self._save_persisted_state()
                except Exception as e:
                    self.log.warning(f"PnL tracking failed for {client_order_id}: {e}")

                # CRITICAL FIX: Remove fill_key from tracking so the same level
                # can get new TP/SL orders on future grid fills.
                # Without this, the _fills_with_exits set grows forever and blocks
                # all repeat fills at the same level from getting exit orders.
                # Order ID format: HG1-TP-L01-timestamp-counter or HG1-SL-S05-timestamp-counter
                try:
                    parts = client_order_id.split("-")
                    if len(parts) >= 3:
                        side_level_part = parts[2]  # e.g., "L01" or "S05"
                        if len(side_level_part) >= 2:
                            side_abbr = side_level_part[0]  # "L" or "S"
                            level_str = side_level_part[1:]  # "01" or "05"
                            side_name = "LONG" if side_abbr == "L" else "SHORT"
                            exit_level = int(level_str)
                            exit_fill_key = f"{side_name}-{exit_level}"
                            with self._fills_lock:
                                if exit_fill_key in self._fills_with_exits:
                                    self._fills_with_exits.discard(exit_fill_key)
                                    self.log.info(
                                        f"[{order_type} CLEANUP] Removed {exit_fill_key} from tracking, "
                                        f"level available for new TP/SL"
                                    )
                            # OCO: Cancel the counterpart order (TP filled → cancel SL, and vice versa)
                            self._cancel_counterpart_exit(exit_fill_key, order_type)

                            # Safety net: if this exit fill closed the position entirely,
                            # cancel ALL remaining exit orders for that side to prevent
                            # orphaned orders from opening unwanted positions.
                            position_id = PositionId(f"{self.instrument_id}-{side_name}")
                            pos = self.cache.position(position_id)
                            if pos is None or pos.quantity == 0 or pos.is_closed:
                                orphans = self._cancel_exit_orders_for_side(side_abbr)
                                if orphans > 0:
                                    self.log.info(
                                        f"[ORPHAN CLEANUP] {side_name} position is flat, "
                                        f"cancelled {orphans} remaining exit orders"
                                    )

                except (ValueError, IndexError) as e:
                    self.log.warning(
                        f"Could not extract fill_key from {order_type} order ID: {client_order_id}, error: {e}"
                    )

                return

            # Parse client_order_id to get level and side
            try:
                parsed = self._parse_cached_order_id(event.client_order_id.value)
                side = parsed["side"]
                level = parsed["level"]
            except (ValueError, KeyError) as e:
                self.log.warning(f"Could not parse client_order_id {event.client_order_id}: {e}")
                return

            # Add null checks for parsed values
            if side is None or level is None:
                self.log.error(f"Parsed side or level is None for {event.client_order_id}")
                return

            # Thread-safe check and add for duplicate prevention - FIXED: check inside lock
            fill_key = f"{side.value}-{level}"
            with self._fills_lock:
                if fill_key in self._fills_with_exits:
                    self.log.debug(f"TP/SL already exist for {fill_key}, skipping creation")
                    return
                # Immediately add to set to claim this fill (prevents race condition)
                self._fills_with_exits.add(fill_key)

            # Calculate TP/SL prices based on grid configuration
            if self._hedge_grid_config is None or self._last_mid is None:
                self.log.warning("Cannot create TP/SL: config or mid price missing")
                return

            fill_price = float(event.last_px)
            fill_qty = float(event.last_qty)

            # Calculate price step using Decimal for precision
            mid_decimal = Decimal(str(self._last_mid))
            fill_price_decimal = Decimal(str(fill_price))
            step_bps = Decimal(str(self._hedge_grid_config.grid.grid_step_bps))
            price_step = mid_decimal * (step_bps / Decimal("10000"))

            # Calculate TP/SL based on side using Decimal
            if side == Side.LONG:
                # LONG: TP above entry, SL below entry
                tp_price_decimal = fill_price_decimal + (Decimal(self._hedge_grid_config.exit.tp_steps) * price_step)
                tp_price = float(tp_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                sl_price_decimal = fill_price_decimal - (Decimal(self._hedge_grid_config.exit.sl_steps) * price_step)
                if sl_price_decimal <= 0:
                    sl_price_decimal = fill_price_decimal * Decimal("0.01")  # Ensure positive
                sl_price = float(sl_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            else:
                # SHORT: TP below entry, SL above entry
                tp_price_decimal = fill_price_decimal - (Decimal(self._hedge_grid_config.exit.tp_steps) * price_step)
                if tp_price_decimal <= 0:
                    tp_price_decimal = fill_price_decimal * Decimal("0.01")  # Ensure positive
                tp_price = float(tp_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                sl_price_decimal = fill_price_decimal + (Decimal(self._hedge_grid_config.exit.sl_steps) * price_step)
                sl_price = float(sl_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

            self.log.info(
                f"[TP/SL CREATION] Creating exit orders for {side} fill @ {fill_price:.2f}: "
                f"TP={tp_price:.2f} ({self._hedge_grid_config.exit.tp_steps} steps), "
                f"SL={sl_price:.2f} ({self._hedge_grid_config.exit.sl_steps} steps)"
            )

            # Check instrument is available before creating orders
            if self._instrument is None:
                self.log.error("Cannot create TP/SL: instrument not initialized")
                # Remove from tracking since we failed to create orders
                with self._fills_lock:
                    self._fills_with_exits.discard(fill_key)
                return

            # Create position_id with side suffix
            position_id = PositionId(f"{self.instrument_id}-{side.value}")

            # CRITICAL FIX: Verify position exists in cache before creating reduce-only orders
            # In backtests, position updates may lag by 1 event cycle
            position = self.cache.position(position_id)

            # Add retry counter for position cache lag handling
            retry_key = f"pos_retry_{fill_key}"

            if position is None or position.quantity <= 0:
                # Track retry attempts
                retry_count = self._position_retry_counts.get(retry_key, 0)

                if retry_count < 3:  # Allow up to 3 retries
                    self._position_retry_counts[retry_key] = retry_count + 1
                    self.log.warning(
                        f"[TP/SL DELAYED] Position not yet in cache for {fill_key}, "
                        f"retry {retry_count + 1}/3 (common in backtests)"
                    )
                    # Remove from tracking so it can be retried on next event
                    with self._fills_lock:
                        self._fills_with_exits.discard(fill_key)
                    return
                # Too many retries, position never appeared
                self.log.error(
                    f"[TP/SL FAILED] Position never appeared in cache for {fill_key} "
                    f"after 3 retries. Removing from tracking to allow future attempts."
                )
                # Clean up retry counter
                del self._position_retry_counts[retry_key]
                # Remove fill_key so future fills at this level can attempt TP/SL again
                with self._fills_lock:
                    self._fills_with_exits.discard(fill_key)
                return

            # Position found, clean up retry counter if it exists
            if retry_key in self._position_retry_counts:
                del self._position_retry_counts[retry_key]

            # Verify position quantity roughly matches fill quantity (allow 1% tolerance)
            position_qty = float(position.quantity)
            if abs(position_qty - fill_qty) > fill_qty * 0.01 and position_qty < fill_qty:
                self.log.warning(
                    f"[TP/SL DELAYED] Position qty {position_qty:.6f} < fill qty {fill_qty:.6f} "
                    f"for {fill_key}, waiting for full position update"
                )
                # Remove from tracking so it can be retried
                with self._fills_lock:
                    self._fills_with_exits.discard(fill_key)
                return

            try:
                # Create TP limit order using fill event timestamp for uniqueness
                tp_order = self._create_tp_order(
                    side=side,
                    quantity=fill_qty,
                    tp_price=tp_price,
                    level=level,  # type: ignore[arg-type]
                    fill_event_ts=event.ts_event,
                )

                # Create SL stop-market order using fill event timestamp for uniqueness
                sl_order = self._create_sl_order(
                    side=side,
                    quantity=fill_qty,
                    sl_price=sl_price,
                    level=level,  # type: ignore[arg-type]
                    fill_event_ts=event.ts_event,
                )

                # Validate orders were created successfully
                if tp_order is None or sl_order is None:
                    self.log.error(f"Failed to create TP or SL order for {fill_key}")
                    # Remove from tracking since we failed
                    with self._fills_lock:
                        self._fills_with_exits.discard(fill_key)
                    return

                # Submit orders — intentionally bypasses risk gates (_execute_add) because:
                # 1. TP/SL are exit orders that reduce exposure, not increase it
                # 2. They must be placed even when drawdown/circuit breaker is active
                # 3. Blocking TP/SL would leave positions unprotected
                self.submit_order(tp_order, position_id=position_id)
                self.submit_order(sl_order, position_id=position_id)

                # Track the TP/SL pair for OCO-like cancellation
                tp_order_id = str(tp_order.client_order_id.value)
                sl_order_id = str(sl_order.client_order_id.value)
                with self._tp_sl_pairs_lock:
                    self._tp_sl_pairs[fill_key] = (tp_order_id, sl_order_id)

                self.log.info(
                    f"[TP/SL SUBMITTED] Successfully submitted TP/SL orders for {fill_key}: "
                    f"TP ID={tp_order.client_order_id}, SL ID={sl_order.client_order_id}"
                )

                # Note: fill_key already added to _fills_with_exits above

            except Exception as e:
                # On any failure, remove from set to allow retry
                with self._fills_lock:
                    self._fills_with_exits.discard(fill_key)
                self.log.error(f"Failed to create/submit TP/SL for {fill_key}: {e}")
                return

            self.log.info(f"Submitted TP/SL orders for level {level}")

        except Exception as e:
            # Critical error handler for entire method
            self.log.error(f"Critical error in on_order_filled: {e}")
            self._handle_critical_error()
            # Re-raise to ensure Nautilus knows about the error
            raise

    # Methods extracted to mixin modules — see:
    #   exit_manager.py, order_events.py, order_executor.py,
    #   ops_api.py, metrics.py, risk_manager.py, state_persistence.py
