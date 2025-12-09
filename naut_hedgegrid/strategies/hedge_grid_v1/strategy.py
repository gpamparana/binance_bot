"""HedgeGridV1 trading strategy implementation."""

import threading
from collections import deque
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    PositionSide,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderFilled,
    OrderRejected,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import LimitOrder, StopMarketOrder
from nautilus_trader.trading.strategy import Strategy

from naut_hedgegrid.config.strategy import HedgeGridConfig, HedgeGridConfigLoader
from naut_hedgegrid.domain.types import (
    Ladder,
    OrderIntent,
    Side,
    parse_client_order_id,
)
from naut_hedgegrid.exchange.precision import PrecisionGuard
from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
from naut_hedgegrid.strategy.detector import Bar as DetectorBar, RegimeDetector
from naut_hedgegrid.strategy.funding_guard import FundingGuard
from naut_hedgegrid.strategy.grid import GridEngine
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff, PostOnlyRetryHandler
from naut_hedgegrid.strategy.policy import PlacementPolicy


class HedgeGridV1(Strategy):
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

        # Position validation
        self._last_balance_check: float = 0.0
        self._balance_check_interval: int = 60_000_000_000  # 60 seconds in nanoseconds
        self._ladder_lock = threading.Lock()  # Thread safety for ladder snapshots

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
            self.log.info(f"Loaded config from {self.config_path}")
        except Exception as e:
            self.log.error(f"Failed to load config from {self.config_path}: {e}")
            return

        # Get instrument from cache
        self._instrument = self.cache.instrument(self.instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrument {self.instrument_id} not found in cache")
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
        )
        self.log.info("Order diff engine initialized")

        # Subscribe to bars
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Subscribed to bars: {self.bar_type}")

        # Initialize metrics tracking
        self._start_time = self.clock.timestamp_ns()
        self._last_bar_time = None
        self._total_fills = 0
        self._maker_fills = 0
        self.log.info("Metrics tracking initialized")

        # Risk management is already initialized in __init__
        # All risk checks (_check_circuit_breaker, _check_drawdown_limit)
        # are called during trading operations

        # Perform warmup if configured
        # The warmup will fetch historical data and pre-warm the regime detector
        self._perform_warmup()

        self.log.info("HedgeGridV1 strategy started successfully")

    def _perform_warmup(self) -> None:
        """
        Perform strategy warmup by fetching historical data.

        This method fetches historical bars from Binance and uses them to
        pre-warm the regime detector, avoiding the need to wait for live bars.
        """
        try:
            # Check if warmup is enabled in config
            if not self.config.enable_warmup:
                self.log.info("Warmup disabled in config")
                return

            # Skip warmup in backtests as they have their own data
            # Backtests are detected by checking the clock type
            # TestClock is used in backtests, LiveClock in live/paper trading
            if hasattr(self.clock, "__class__") and "Test" in self.clock.__class__.__name__:
                self.log.debug("Skipping warmup in backtest mode")
                return

            # Try to import the warmup module
            try:
                from naut_hedgegrid.config.venue import VenueConfig, VenueConfigLoader
                from naut_hedgegrid.warmup import BinanceDataWarmer
            except ImportError as e:
                self.log.warning(f"Warmup module not available: {e}, starting without warmup")
                return

            # Get API credentials from environment
            import os

            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")

            if not api_key or not api_secret:
                self.log.warning("No Binance API credentials found, skipping warmup")
                return

            # Try to load venue config from file or create minimal config
            try:
                # First try to load from standard locations
                import pathlib

                # Check common venue config paths
                config_paths = [
                    pathlib.Path("configs/venues/binance_testnet.yaml"),
                    pathlib.Path("configs/venues/binance.yaml"),
                ]

                venue_config = None
                for config_path in config_paths:
                    if config_path.exists():
                        try:
                            venue_config = VenueConfigLoader.load(str(config_path))
                            break
                        except Exception:
                            continue

                # If no config found or testnet flag differs, create minimal config
                if venue_config is None or venue_config.api.testnet != self.config.testnet:
                    # Create minimal config dict matching VenueConfig schema
                    venue_config_dict = {
                        "venue": {
                            "name": "BINANCE",
                            "venue_type": "futures",
                            "account_type": "PERPETUAL_LINEAR",
                        },
                        "api": {
                            "api_key": api_key,
                            "api_secret": api_secret,
                            "testnet": self.config.testnet,
                        },
                        "trading": {
                            "hedge_mode": True,
                            "leverage": 1,
                            "margin_type": "CROSSED",
                        },
                        "risk": {
                            "max_leverage": 20,
                            "min_order_size_usdt": 5.0,
                            "max_order_size_usdt": 100000.0,
                        },
                        "precision": {
                            "price_precision": 2,
                            "quantity_precision": 3,
                            "min_notional": 5.0,
                        },
                        "rate_limits": {
                            "orders_per_second": 5,
                            "orders_per_minute": 100,
                            "weight_per_minute": 1200,
                        },
                        "websocket": {
                            "ping_interval": 30,
                            "reconnect_timeout": 60,
                            "max_reconnect_attempts": 10,
                        },
                    }
                    venue_config = VenueConfig.model_validate(venue_config_dict)

            except Exception as e:
                self.log.warning(f"Could not create venue config for warmup: {e}")
                return

            # Extract symbol from instrument ID
            symbol = str(self.instrument_id).split("-")[0]

            # Calculate bars needed for warmup
            regime_cfg = self._hedge_grid_config.regime
            warmup_bars = max(regime_cfg.ema_slow + 20, 70)

            self.log.info(
                f"Starting warmup: fetching {warmup_bars} historical bars for {symbol} "
                f"(testnet={self.config.testnet})"
            )

            # Fetch historical data
            with BinanceDataWarmer(venue_config) as warmer:
                historical_bars = warmer.fetch_detector_bars(
                    symbol=symbol,
                    num_bars=warmup_bars,
                    interval="1m",
                )

                if historical_bars:
                    self.log.info(f"✓ Fetched {len(historical_bars)} historical bars")
                    self.warmup_regime_detector(historical_bars)
                else:
                    self.log.warning("No historical bars fetched, starting without warmup")

        except Exception as e:
            self.log.warning(f"Warmup failed: {e}. Starting without warmup.")

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
                self.log.debug(
                    f"Warmup progress: {i + 1}/{len(historical_bars)} bars, "
                    f"regime={regime}, warm={is_warm}"
                )

        # Final status
        final_regime = self._regime_detector.current()
        is_warm = self._regime_detector.is_warm

        if is_warm:
            try:
                ema_fast_val = (
                    self._regime_detector.ema_fast.value
                    if self._regime_detector.ema_fast.value
                    else 0
                )
                ema_slow_val = (
                    self._regime_detector.ema_slow.value
                    if self._regime_detector.ema_slow.value
                    else 0
                )
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

        self.log.info("HedgeGridV1 strategy stopped")

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

        # Log regime and price with error handling
        try:
            warm_status = self._regime_detector.is_warm
            self.log.info(f"Bar: close={mid:.2f}, regime={regime}, warm={warm_status}")
        except Exception as e:
            # Fallback logging if there's an issue with property access
            self.log.warning(f"Error logging bar info: {e}. Bar close={mid:.2f}")

        # Check if detector is warm enough for trading
        if not self._regime_detector.is_warm:
            self.log.info("Regime detector not warm yet, skipping trading")
            return

        # Check if grid recentering needed
        recenter_needed = GridEngine.recenter_needed(
            mid=mid,
            last_center=self._grid_center,
            cfg=self._hedge_grid_config,
        )

        if recenter_needed:
            self.log.info(f"Grid recentering triggered at mid={mid:.2f}")
            self._grid_center = mid

        # Build ladders
        ladders = GridEngine.build_ladders(
            mid=self._grid_center,  # Use stable grid center, not current price
            cfg=self._hedge_grid_config,
            regime=regime,
        )

        self.log.info(
            f"Built {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Log grid center vs current price for debugging
        deviation_bps = abs(mid - self._grid_center) / self._grid_center * 10000
        self.log.debug(
            f"Grid center: {self._grid_center:.2f}, Current mid: {mid:.2f}, "
            f"Deviation: {deviation_bps:.1f} bps"
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

        self.log.info(
            f"After policy: {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Apply funding guard
        now = datetime.now(tz=UTC)
        ladders = self._funding_guard.adjust_ladders(ladders=ladders, now=now)

        self.log.info(
            f"After funding: {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

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
        if current_time % 300_000_000_000 < 60_000_000_000:  # Within first minute of 5-min window
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

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle order filled event with comprehensive error recovery.

        When a grid order fills:
        1. Parse client_order_id to get original rung metadata
        2. Retrieve rung TP/SL prices from grid calculation
        3. Create TP limit order (reduce-only) at tp_price
        4. Create SL stop-market order (reduce-only) at sl_price
        5. Submit both with correct position_id suffix

        Args:
            event: Order filled event

        """
        try:
            # Check if we're in critical error state
            if self._critical_error:
                self.log.warning(
                    f"Strategy in critical error state, ignoring fill event: {event.client_order_id}"
                )
                return

            self.log.info(
                f"[FILL EVENT] Order filled: {event.client_order_id} @ {event.last_px}, qty={event.last_qty}"
            )

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
                tp_price_decimal = fill_price_decimal + (
                    Decimal(self._hedge_grid_config.exit.tp_steps) * price_step
                )
                tp_price = float(tp_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                sl_price_decimal = fill_price_decimal - (
                    Decimal(self._hedge_grid_config.exit.sl_steps) * price_step
                )
                if sl_price_decimal <= 0:
                    sl_price_decimal = fill_price_decimal * Decimal("0.01")  # Ensure positive
                sl_price = float(sl_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            else:
                # SHORT: TP below entry, SL above entry
                tp_price_decimal = fill_price_decimal - (
                    Decimal(self._hedge_grid_config.exit.tp_steps) * price_step
                )
                if tp_price_decimal <= 0:
                    tp_price_decimal = fill_price_decimal * Decimal("0.01")  # Ensure positive
                tp_price = float(tp_price_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                sl_price_decimal = fill_price_decimal + (
                    Decimal(self._hedge_grid_config.exit.sl_steps) * price_step
                )
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
                    f"[TP/SL FAILED] Position never appeared in cache for {fill_key} after 3 retries"
                )
                # Clean up retry counter
                del self._position_retry_counts[retry_key]
                # Keep fill_key in set to prevent further attempts
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
                # Create TP limit order (reduce-only) using fill event timestamp for uniqueness
                tp_order = self._create_tp_order(
                    side=side,
                    quantity=fill_qty,
                    tp_price=tp_price,
                    position_id=position_id,
                    level=level,  # type: ignore[arg-type]
                    fill_event_ts=event.ts_event,  # Use fill event timestamp for unique IDs
                )

                # Create SL stop-market order (reduce-only) using fill event timestamp for uniqueness
                sl_order = self._create_sl_order(
                    side=side,
                    quantity=fill_qty,
                    sl_price=sl_price,
                    position_id=position_id,
                    level=level,  # type: ignore[arg-type]
                    fill_event_ts=event.ts_event,  # Use fill event timestamp for unique IDs
                )

                # Validate orders were created successfully
                if tp_order is None or sl_order is None:
                    self.log.error(f"Failed to create TP or SL order for {fill_key}")
                    # Remove from tracking since we failed
                    with self._fills_lock:
                        self._fills_with_exits.discard(fill_key)
                    return

                # Submit orders
                self.submit_order(tp_order, position_id=position_id)
                self.submit_order(sl_order, position_id=position_id)

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

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """
        Handle order accepted event - remove from retry queue on success.

        Logs order acceptance. Order tracking is done via cache queries.

        Args:
            event: Order accepted event

        """
        client_order_id = str(event.client_order_id.value)

        # Remove from pending retries (success!)
        if client_order_id in self._pending_retries:
            intent = self._pending_retries[client_order_id]
            if intent.retry_count > 0:
                self.log.info(
                    f"Order {client_order_id} accepted after {intent.retry_count} retries "
                    f"(original price: {intent.original_price}, final price: {intent.price})"
                )
            del self._pending_retries[client_order_id]
            if self._retry_handler is not None:
                self._retry_handler.clear_history(client_order_id)

        # Log order acceptance with detail for TP/SL orders
        order_id = event.client_order_id.value
        if order_id.startswith(self._strategy_name):
            if "-TP-" in order_id:
                self.log.info(f"[TP ACCEPTED] Take-profit order accepted: {event.client_order_id}")
            elif "-SL-" in order_id:
                self.log.info(f"[SL ACCEPTED] Stop-loss order accepted: {event.client_order_id}")
            else:
                self.log.debug(f"Grid order accepted: {event.client_order_id}")

                # Add grid order to internal cache for O(1) lookups
                order = self.cache.order(event.client_order_id)
                if order:
                    try:
                        parsed = self._parse_cached_order_id(client_order_id)
                        live_order = LiveOrder(
                            client_order_id=client_order_id,
                            side=parsed["side"],  # type: ignore[arg-type]
                            price=float(order.price) if hasattr(order, "price") else 0.0,
                            qty=float(order.quantity),
                            status="OPEN",
                        )
                        with self._grid_orders_lock:
                            self._grid_orders_cache[client_order_id] = live_order
                    except (ValueError, KeyError) as e:
                        self.log.warning(f"Could not parse order for caching: {e}")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """
        Handle order canceled event.

        Logs order cancellation. Order tracking is done via cache queries.

        Args:
            event: Order canceled event

        """
        # Log order cancellation
        client_order_id = str(event.client_order_id.value)
        if client_order_id.startswith(self._strategy_name):
            self.log.debug(f"Order canceled: {event.client_order_id}")

            # Remove grid order from internal cache
            if "-TP-" not in client_order_id and "-SL-" not in client_order_id:
                with self._grid_orders_lock:
                    self._grid_orders_cache.pop(client_order_id, None)

    def on_order_rejected(self, event: OrderRejected) -> None:
        """
        Handle order rejection with retry logic for post-only failures.

        When a post-only order is rejected because it would cross the spread,
        this handler:
        1. Adjusts price by one tick away from spread
        2. Retries up to N times (configured)
        3. Logs each attempt with reason
        4. Abandons order after max attempts exhausted

        Args:
            event: OrderRejected event from NautilusTrader

        """
        try:
            client_order_id = str(event.client_order_id.value)
            rejection_reason = str(event.reason) if hasattr(event, "reason") else "Unknown"

            # Idempotency check: prevent duplicate processing of same rejection event
            # (Nautilus may call this handler multiple times for same rejection)
            # Thread-safe access using lock (initialized in __init__)
            rejection_key = f"{client_order_id}_{event.ts_event}"
            with self._rejections_lock:
                if rejection_key in self._processed_rejections:
                    # Already processed this exact rejection event, skip duplicate
                    return
                self._processed_rejections.add(rejection_key)

                # Clean up old rejection keys (keep only last 100 to prevent memory leak)
                if len(self._processed_rejections) > 100:
                    # Remove oldest entries, keep most recent 50
                    to_remove = list(self._processed_rejections)[:-50]
                    for key in to_remove:
                        self._processed_rejections.discard(key)

            # Also clean up pending_retries if it gets too large
            if len(self._pending_retries) > 50:
                self.log.warning(
                    f"Pending retries queue too large ({len(self._pending_retries)}), cleaning up old entries"
                )
                # Remove entries that have been pending for too long
                keys_to_remove = list(self._pending_retries.keys())[:-25]
                for key in keys_to_remove:
                    del self._pending_retries[key]

            # Enhanced logging for TP/SL rejections and cleanup to allow retry
            if "-TP-" in client_order_id or "-SL-" in client_order_id:
                order_type = "TP" if "-TP-" in client_order_id else "SL"
                self.log.error(
                    f"[{order_type} REJECTED] {order_type} order rejected: {client_order_id}, reason: {rejection_reason}"
                )

                # Extract fill_key from order ID to allow retry (Phase 2.2 fix)
                # Order ID format: HG1-TP-L01-timestamp-counter or HG1-SL-S05-timestamp-counter
                try:
                    parts = client_order_id.split("-")
                    if len(parts) >= 3:
                        side_level_part = parts[2]  # e.g., "L01" or "S05"
                        if len(side_level_part) >= 2:
                            side_abbr = side_level_part[0]  # "L" or "S"
                            level_str = side_level_part[1:]  # "01" or "05"
                            side = "LONG" if side_abbr == "L" else "SHORT"
                            level = int(level_str)
                            fill_key = f"{side}-{level}"

                            # Remove from tracking to allow retry on next fill
                            with self._fills_lock:
                                if fill_key in self._fills_with_exits:
                                    self._fills_with_exits.discard(fill_key)
                                    self.log.info(
                                        f"[{order_type} RETRY] Removed {fill_key} from tracking to allow TP/SL retry"
                                    )
                except (ValueError, IndexError) as e:
                    self.log.warning(
                        f"Could not extract fill_key from rejected order ID: {client_order_id}, error: {e}"
                    )
            else:
                self.log.warning(
                    f"Grid order rejected: {client_order_id}, reason: {rejection_reason}"
                )

            # Check if retry handler is initialized
            if self._retry_handler is None or not self._retry_handler.enabled:
                return

            # Check if this order is in retry queue
            if client_order_id not in self._pending_retries:
                # Not tracking this order (maybe retry is disabled or already exhausted)
                return

            intent = self._pending_retries[client_order_id]

            # Don't retry Binance -5022 errors (post-only would trade)
            # These errors mean the order price crossed the spread and would execute immediately.
            # Retrying with small price adjustments won't help in fast-moving markets.
            # Better to let the next bar recalculate the grid with updated market price.
            if "-5022" in rejection_reason:
                self.log.debug(
                    f"Order {client_order_id} rejected with -5022 (post-only would trade), "
                    f"abandoning retry - will recalculate grid on next bar"
                )
                del self._pending_retries[client_order_id]
                if self._retry_handler:
                    self._retry_handler.clear_history(client_order_id)
                return

            # Check if retry is warranted for this rejection type
            if not self._retry_handler.should_retry(rejection_reason):
                self.log.warning(
                    f"Order {client_order_id} rejected for non-retryable reason: {rejection_reason}"
                )
                del self._pending_retries[client_order_id]
                self._retry_handler.clear_history(client_order_id)
                return

            # Check retry limit
            if intent.retry_count >= self._hedge_grid_config.execution.retry_attempts:  # type: ignore[union-attr]
                self.log.warning(
                    f"Order {client_order_id} exhausted {intent.retry_count} retries, abandoning"
                )
                del self._pending_retries[client_order_id]
                self._retry_handler.clear_history(client_order_id)
                return

            # Adjust price for retry
            new_attempt = intent.retry_count + 1
            adjusted_price = self._retry_handler.adjust_price_for_retry(
                original_price=intent.original_price or intent.price or 0.0,
                side=intent.side or Side.LONG,
                attempt=new_attempt,
            )

            # Record this retry attempt
            self._retry_handler.record_attempt(
                client_order_id=client_order_id,
                attempt=new_attempt,
                original_price=intent.original_price or intent.price or 0.0,
                adjusted_price=adjusted_price,
                reason=rejection_reason,
            )

            # Create new intent with adjusted price AND NEW CLIENT_ORDER_ID
            # Note: We need to use dataclass.replace since OrderIntent is frozen
            from dataclasses import replace

            # Generate new unique order ID for retry
            # Extract base order ID without any retry suffixes
            base_order_id = (
                client_order_id.split("-retry")[0]
                if "-retry" in client_order_id
                else client_order_id
            )
            base_order_id = base_order_id.split("-R")[0] if "-R" in base_order_id else base_order_id

            # Create compact retry ID that stays under 36 char limit
            # Format: {base_id}-R{attempt} (e.g., HG1-LONG-01-1761018780259-24 -> HG1-LONG-01-1761018780259-R1)
            new_client_order_id = f"{base_order_id}-R{new_attempt}"

            # Validate length to prevent Binance rejection
            if len(new_client_order_id) > 36:
                # If still too long, truncate the timestamp portion
                parts = base_order_id.split("-")
                if len(parts) >= 4:
                    # Shorten timestamp from 13 to 10 digits (millisecond precision instead of nanosecond)
                    parts[3] = parts[3][:10]
                    base_order_id = "-".join(parts)
                    new_client_order_id = f"{base_order_id}-R{new_attempt}"

            self.log.debug(
                f"Generated retry order ID: {new_client_order_id} (length: {len(new_client_order_id)})"
            )

            new_intent = replace(
                intent,
                client_order_id=new_client_order_id,  # NEW ID for retry
                price=adjusted_price,
                retry_count=new_attempt,
                original_price=intent.original_price or intent.price,
                metadata={**intent.metadata, "retry_attempt": str(new_attempt)},
            )

            # Remove old order ID from pending retries, add new one
            del self._pending_retries[client_order_id]
            self._pending_retries[new_client_order_id] = new_intent

            self.log.info(
                f"Retrying order (attempt {new_attempt}/"
                f"{self._hedge_grid_config.execution.retry_attempts}): "  # type: ignore[union-attr]
                f"old_id={client_order_id}, new_id={new_client_order_id}, "
                f"adjusted price {intent.price} -> {adjusted_price}"
            )

            # Submit retry (with delay if configured)
            if self._hedge_grid_config.execution.retry_delay_ms > 0:  # type: ignore[union-attr]
                # Schedule delayed retry using Nautilus clock
                delay_ns = self._hedge_grid_config.execution.retry_delay_ms * 1_000_000  # type: ignore[union-attr]  # ms to ns

                # Create callback that captures the intent
                # Note: Nautilus clock.set_timer_ns expects a regular function, not async
                def retry_callback() -> None:
                    """Execute retry attempt for order."""
                    self._execute_add(new_intent)

                # Use clock to schedule one-time callback
                # Nautilus 1.220.0 requires set_time_alert_ns for one-time callbacks
                alert_time_ns = self.clock.timestamp_ns() + delay_ns

                # Wrap callback to match TimeEvent signature expected by Nautilus
                def timer_callback(event) -> None:
                    """Execute retry attempt for order after delay."""
                    self._execute_add(new_intent)

                self.clock.set_time_alert_ns(
                    name=f"retry_{client_order_id}_{new_attempt}",
                    alert_time_ns=alert_time_ns,
                    callback=timer_callback,
                )
            else:
                # Immediate retry
                self._execute_add(new_intent)

        except Exception as e:
            # Non-critical error handling
            self.log.error(f"Error in on_order_rejected: {e}")
            # Continue processing - non-critical error

    def on_order_denied(self, event) -> None:
        """
        Handle order denied event - clean up denied orders from retry tracking.

        When an order is DENIED (usually due to duplicate order ID), we need to:
        1. Remove it from pending retries to prevent infinite retry loops
        2. Clear retry history
        3. Log the error

        Args:
            event: OrderDenied event from NautilusTrader

        """
        client_order_id = str(event.client_order_id.value)
        reason = str(event.reason) if hasattr(event, "reason") else "Unknown"

        # Enhanced logging for TP/SL denials
        if "-TP-" in client_order_id:
            # Downgrade to debug during optimization - these are expected in backtests due to position cache lag
            self.log.debug(
                f"[TP DENIED] Take-profit order denied: {client_order_id}, reason: {reason}"
            )
        elif "-SL-" in client_order_id:
            # Downgrade to debug during optimization - these are expected in backtests due to position cache lag
            self.log.debug(
                f"[SL DENIED] Stop-loss order denied: {client_order_id}, reason: {reason}"
            )
        else:
            self.log.error(f"Grid order denied: {client_order_id}, reason: {reason}")

        # Remove from pending retries (order ID is invalid, cannot retry)
        if client_order_id in self._pending_retries:
            del self._pending_retries[client_order_id]
            if self._retry_handler is not None:
                self._retry_handler.clear_history(client_order_id)
            self.log.debug(f"Cleaned up denied order {client_order_id} from retry tracking")

    def _execute_diff(self, diff_result) -> None:
        """
        Execute diff operations to reconcile desired vs live state.

        Executes in order:
        1. Cancels (remove unwanted orders)
        2. Replaces (cancel + recreate modified orders)
        3. Adds (create new orders)

        Args:
            diff_result: DiffResult with operations to execute

        """
        if diff_result.is_empty:
            self.log.debug("No diff operations needed")
            return

        # Execute cancels
        for cancel_intent in diff_result.cancels:
            self._execute_cancel(cancel_intent)

        # Execute replaces (cancel old + create new)
        for replace_intent in diff_result.replaces:
            self._execute_replace(replace_intent)

        # Execute adds
        for add_intent in diff_result.adds:
            self._execute_add(add_intent)

    def _execute_cancel(self, intent: OrderIntent) -> None:
        """
        Execute cancel operation.

        Args:
            intent: Cancel intent with client_order_id

        """
        # Find order in cache
        order = self.cache.order(ClientOrderId(intent.client_order_id))
        if order is None:
            self.log.warning(f"Cannot cancel: order {intent.client_order_id} not in cache")
            return

        if not order.is_open:
            self.log.debug(f"Order {intent.client_order_id} already closed, skipping cancel")
            return

        self.cancel_order(order)
        self.log.debug(f"Canceled order: {intent.client_order_id}")

    def _execute_replace(self, intent: OrderIntent) -> None:
        """
        Execute replace operation (cancel old + create new).

        Args:
            intent: Replace intent with old and new order params

        """
        # Cancel old order
        self._execute_cancel(OrderIntent.cancel(intent.client_order_id))

        # Create new order with updated params
        if intent.replace_with is None or intent.side is None:
            self.log.warning(f"Invalid replace intent: {intent}")
            return

        new_intent = OrderIntent.create(
            client_order_id=intent.replace_with,
            side=intent.side,
            price=intent.price or 0.0,
            qty=intent.qty or 0.0,
            metadata=intent.metadata,
        )
        self._execute_add(new_intent)

    def _execute_add(self, intent: OrderIntent) -> None:
        """
        Execute add operation (create new limit order) with post-only retry tracking.

        Args:
            intent: Create intent with order parameters

        """
        if self._instrument is None or intent.side is None:
            self.log.warning("Cannot create order: instrument or side missing")
            return

        # Create limit order (this appends counter to client_order_id)
        order = self._create_limit_order(intent, self._instrument)

        # Create position_id with side suffix
        position_id = PositionId(f"{self.instrument_id}-{intent.side.value}")

        # Submit order
        self.submit_order(order, position_id=position_id)

        # Get the actual order ID (which includes the counter suffix)
        actual_order_id = str(order.client_order_id.value)
        self.log.debug(f"Created order: {actual_order_id} @ {intent.price}")

        # Track order for potential retry using the ACTUAL order ID
        # Note: We need to use the actual ID since events will reference it
        if self._retry_handler is not None and self._retry_handler.enabled:
            self._pending_retries[actual_order_id] = intent

    def _create_limit_order(self, intent: OrderIntent, instrument: Instrument) -> LimitOrder:
        """
        Create Nautilus LimitOrder from OrderIntent.

        Args:
            intent: Order intent with parameters
            instrument: Trading instrument

        Returns:
            LimitOrder ready to submit

        """
        # Convert side
        order_side = OrderSide.BUY if intent.side == Side.LONG else OrderSide.SELL

        # Generate unique client_order_id by appending counter
        # This ensures grid orders have unique IDs even if created at same timestamp
        with self._order_id_lock:
            self._order_id_counter += 1
            unique_client_order_id = f"{intent.client_order_id}-{self._order_id_counter}"

        # Create order
        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=Quantity(intent.qty, precision=instrument.size_precision),
            price=Price(intent.price, precision=instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            post_only=True,  # Maker-only orders
            client_order_id=ClientOrderId(unique_client_order_id),
        )

        return order

    def _create_tp_order(
        self,
        side: Side,
        quantity: float,
        tp_price: float,
        position_id: PositionId,
        level: int,
        fill_event_ts: int,
    ) -> LimitOrder:
        """
        Create take-profit limit order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            tp_price: Take profit price
            position_id: Position ID with side suffix
            level: Grid level for order ID
            fill_event_ts: Timestamp from fill event (nanoseconds) for unique ID generation

        Returns:
            Reduce-only limit order at TP price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # TP order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate unique client_order_id for TP with counter
        # IMPORTANT: Binance limits order IDs to 36 characters
        with self._order_id_lock:
            self._order_id_counter += 1
            counter = self._order_id_counter

        # Use fill event timestamp in milliseconds (13 chars) for uniqueness
        # This ensures each fill event gets a unique ID even at same level
        timestamp_ms = fill_event_ts // 1_000_000

        # Shorten side name: LONG->L, SHORT->S
        side_abbr = "L" if side == Side.LONG else "S"

        # Format: HG1-TP-L01-1234567890123-1 (max ~30 chars)
        client_order_id_str = (
            f"{self._strategy_name}-TP-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"
        )

        # Validate length (Binance limit is 36 chars)
        if len(client_order_id_str) > 36:
            self.log.error(
                f"TP order ID too long ({len(client_order_id_str)} chars): {client_order_id_str}"
            )
            # Fallback: use even shorter format
            client_order_id_str = f"TP-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        # Round TP price to instrument tick size to prevent Binance -4014 error
        if self._precision_guard:
            tp_price = self._precision_guard.clamp_price(tp_price)

        order = self.order_factory.limit(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            price=Price(tp_price, precision=self._instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=ClientOrderId(client_order_id_str),
        )

        return order

    def _create_sl_order(
        self,
        side: Side,
        quantity: float,
        sl_price: float,
        position_id: PositionId,
        level: int,
        fill_event_ts: int,
    ) -> StopMarketOrder:
        """
        Create stop-loss stop-market order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            sl_price: Stop loss trigger price
            position_id: Position ID with side suffix
            level: Grid level for order ID
            fill_event_ts: Timestamp from fill event (nanoseconds) for unique ID generation

        Returns:
            Reduce-only stop-market order at SL price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # SL order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate unique client_order_id for SL with counter
        # IMPORTANT: Binance limits order IDs to 36 characters
        with self._order_id_lock:
            self._order_id_counter += 1
            counter = self._order_id_counter

        # Use fill event timestamp in milliseconds (13 chars) for uniqueness
        # This ensures each fill event gets a unique ID even at same level
        timestamp_ms = fill_event_ts // 1_000_000

        # Shorten side name: LONG->L, SHORT->S
        side_abbr = "L" if side == Side.LONG else "S"

        # Format: HG1-SL-L01-1234567890123-1 (max ~30 chars)
        client_order_id_str = (
            f"{self._strategy_name}-SL-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"
        )

        # Validate length (Binance limit is 36 chars)
        if len(client_order_id_str) > 36:
            self.log.error(
                f"SL order ID too long ({len(client_order_id_str)} chars): {client_order_id_str}"
            )
            # Fallback: use even shorter format
            client_order_id_str = f"SL-{side_abbr}{level:02d}-{timestamp_ms}-{counter}"

        # Round SL price to instrument tick size for precision
        if self._precision_guard:
            sl_price = self._precision_guard.clamp_price(sl_price)

        # Validate SL price against current market to prevent immediate trigger
        # This can happen in volatile markets or when fills occur at bar boundaries
        if self._last_mid is not None:
            current_mid = self._last_mid

            if order_side == OrderSide.BUY:  # Closing SHORT position
                # SL should be ABOVE current market to trigger on upward move
                if sl_price <= current_mid:
                    adjusted_price = current_mid * 1.0005
                    self.log.warning(
                        f"[SL ADJUST] Stop-loss {sl_price:.2f} at/below market "
                        f"{current_mid:.2f}, adjusting to {adjusted_price:.2f} (+0.05%)"
                    )
                    # Adjust to slightly above current market
                    sl_price = adjusted_price  # 0.05% buffer
                    if self._precision_guard:
                        sl_price = self._precision_guard.clamp_price(sl_price)

            elif order_side == OrderSide.SELL:  # Closing LONG position
                # SL should be BELOW current market to trigger on downward move
                if sl_price >= current_mid:
                    adjusted_price = current_mid * 0.9995
                    self.log.warning(
                        f"[SL ADJUST] Stop-loss {sl_price:.2f} at/above market "
                        f"{current_mid:.2f}, adjusting to {adjusted_price:.2f} (-0.05%)"
                    )
                    # Adjust to slightly below current market
                    sl_price = adjusted_price  # 0.05% buffer
                    if self._precision_guard:
                        sl_price = self._precision_guard.clamp_price(sl_price)

        order = self.order_factory.stop_market(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            trigger_price=Price(sl_price, precision=self._instrument.price_precision),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=ClientOrderId(client_order_id_str),
        )

        return order

    def venue_order_id_to_client_order_id(self, client_order_id_str: str) -> ClientOrderId:
        """
        Helper to convert string client_order_id to ClientOrderId.

        Args:
            client_order_id_str: Client order ID as string

        Returns:
            ClientOrderId object for Nautilus compatibility

        """
        return ClientOrderId(client_order_id_str)

    def _parse_cached_order_id(self, client_order_id: str) -> dict:
        """
        Parse client order ID with instance-level caching to avoid repeated parsing.

        Uses instance-level dict cache instead of @lru_cache to prevent
        cache collision between multiple strategy instances.

        Args:
            client_order_id: Client order ID string

        Returns:
            Parsed order ID dict

        """
        # Check instance-level cache first
        if client_order_id in self._parsed_order_id_cache:
            return self._parsed_order_id_cache[client_order_id]

        # Parse and cache result
        result = parse_client_order_id(client_order_id)
        self._parsed_order_id_cache[client_order_id] = result

        # Prevent memory leak: limit cache size to 1000 entries
        if len(self._parsed_order_id_cache) > 1000:
            # Remove oldest entries (first 200)
            keys_to_remove = list(self._parsed_order_id_cache.keys())[:200]
            for key in keys_to_remove:
                del self._parsed_order_id_cache[key]

        return result

    def _get_live_grid_orders(self) -> list[LiveOrder]:
        """
        Get live grid orders from internal cache (O(1) performance).

        Returns:
            List of LiveOrder objects for grid orders only (not TP/SL)

        Note:
            This method now uses an internal cache that is updated in real-time
            via order event handlers (on_order_accepted, on_order_canceled, on_order_filled).
            This provides O(1) performance instead of O(n) iteration through all orders.
        """
        with self._grid_orders_lock:
            return list(self._grid_orders_cache.values())

    # =====================================================================
    # OPERATIONAL CONTROLS INTEGRATION
    # =====================================================================

    def get_operational_metrics(self) -> dict:
        """
        Return current operational metrics for monitoring.

        Called periodically by OperationsManager to update Prometheus gauges.
        Note: Cache queries are thread-safe. Simple reads of _total_fills,
        _maker_fills are atomic. Only complex operations need locking.

        Returns:
            Dictionary containing operational metrics for Prometheus export

        """
        # Cache queries and calculations are safe without locks
        return {
            # Position metrics
            "long_inventory_usdt": self._calculate_inventory("long"),
            "short_inventory_usdt": self._calculate_inventory("short"),
            "net_inventory_usdt": self._calculate_net_inventory(),
            # Grid metrics
            "active_rungs_long": len(self._get_active_rungs("long")),
            "active_rungs_short": len(self._get_active_rungs("short")),
            "open_orders_count": len(self._get_live_grid_orders()),
            # Risk metrics
            "margin_ratio": self._get_margin_ratio(),
            "maker_ratio": self._calculate_maker_ratio(),
            # Funding metrics
            "funding_rate_current": self._get_current_funding_rate(),
            "funding_cost_1h_projected_usdt": self._project_funding_cost_1h(),
            # PnL metrics
            "realized_pnl_usdt": self._get_realized_pnl(),
            "unrealized_pnl_usdt": self._get_unrealized_pnl(),
            "total_pnl_usdt": self._get_total_pnl(),
            # System health
            "uptime_seconds": self._get_uptime_seconds(),
            "last_bar_timestamp": (self._last_bar_time.timestamp() if self._last_bar_time else 0.0),
        }

    def attach_kill_switch(self, kill_switch) -> None:
        """
        Attach kill switch for monitoring.

        Args:
            kill_switch: Kill switch instance from ops module

        """
        self._kill_switch = kill_switch
        self.log.info("Kill switch attached to strategy")

    def flatten_side(self, side: str) -> dict:
        """
        Flatten positions for given side (called by kill switch).

        This method cancels all open orders and submits market orders to close
        positions for the specified side(s). Safe to call from API threads
        since all operations use thread-safe Nautilus cache.

        Args:
            side: "long", "short", or "both"

        Returns:
            dict with cancelled orders and closing positions info

        """
        result = {
            "cancelled_orders": 0,
            "closing_positions": [],
        }

        sides = ["long", "short"] if side == "both" else [side]

        for s in sides:
            # Cancel orders (cache queries are thread-safe)
            cancelled = self._cancel_side_orders(s)
            result["cancelled_orders"] += cancelled

            # Close position (cache queries are thread-safe)
            position_info = self._close_side_position(s)
            if position_info:
                result["closing_positions"].append(position_info)

        return result

    def set_throttle(self, throttle: float) -> None:
        """
        Adjust strategy aggressiveness (0.0 = passive, 1.0 = aggressive).

        This modifies the placement policy to scale quantities. Can be called
        from API control endpoints.

        Args:
            throttle: Value between 0.0 and 1.0

        Raises:
            ValueError: If throttle not in valid range

        """
        if not 0.0 <= throttle <= 1.0:
            msg = f"Throttle must be between 0.0 and 1.0, got {throttle}"
            raise ValueError(msg)

        # Simple float assignment is atomic in Python
        self._throttle = throttle

        self.log.info(f"Throttle set to {throttle:.2f}")

    def get_ladders_snapshot(self) -> dict:
        """
        Return current grid ladder state for API.

        Returns:
            Dictionary with current ladder state

        """
        # Use lock to ensure atomic read of multiple related state variables
        with self._ladder_lock:
            # Check if ladders are initialized
            if self._last_long_ladder is None and self._last_short_ladder is None:
                return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

            # Create snapshot while holding lock to ensure consistency
            return {
                "timestamp": self.clock.timestamp_ns(),
                "mid_price": self._last_mid or 0.0,
                "long_ladder": [
                    {"price": r.price, "qty": r.qty, "side": str(r.side)}
                    for r in (self._last_long_ladder.rungs if self._last_long_ladder else [])
                ],
                "short_ladder": [
                    {"price": r.price, "qty": r.qty, "side": str(r.side)}
                    for r in (self._last_short_ladder.rungs if self._last_short_ladder else [])
                ],
            }

    # =====================================================================
    # HELPER METHODS FOR OPERATIONAL METRICS
    # =====================================================================

    def _calculate_inventory(self, side: str) -> float:
        """Calculate inventory in quote currency for given side."""
        position_id_str = f"{self.instrument_id}-{side.upper()}"
        position_id = PositionId(position_id_str)
        position = self.cache.position(position_id)

        if position and position.quantity > 0:
            # Return notional value in USDT
            return abs(float(position.quantity) * float(position.avg_px_open))
        return 0.0

    def _calculate_net_inventory(self) -> float:
        """Net inventory = long - short."""
        return self._calculate_inventory("long") - self._calculate_inventory("short")

    def _get_active_rungs(self, side: str) -> list:
        """Get list of active grid rungs for given side."""
        open_orders = self._get_live_grid_orders()
        position_suffix = f"-{side.upper()}"
        active_rungs = []

        for order in open_orders:
            if position_suffix in str(order.client_order_id):
                # Parse rung number from client_order_id
                try:
                    parsed = self._parse_cached_order_id(order.client_order_id)
                    level = parsed.get("level")
                    if level is not None:
                        active_rungs.append(level)
                except (ValueError, KeyError):
                    continue

        return active_rungs

    def _get_margin_ratio(self) -> float:
        """
        Get current margin ratio from account.

        Note: This requires proper account object access. Currently returns 0.0
        as placeholder. In production, implement based on Nautilus account structure.
        """
        # TODO: Implement once account object structure is confirmed
        # account = self.cache.account(self.account_id)
        # if account:
        #     return float(account.margin_used) / float(account.margin_available)
        return 0.0

    def _calculate_maker_ratio(self) -> float:
        """Calculate ratio of maker fills vs total fills."""
        if self._total_fills == 0:
            return 1.0  # Default to 1.0 (all maker) when no fills yet

        return self._maker_fills / self._total_fills

    def _get_current_funding_rate(self) -> float:
        """
        Get current funding rate from market data.

        Note: This requires subscribing to funding rate updates. Currently returns 0.0
        as placeholder. In production, implement based on Nautilus funding rate data.
        """
        # TODO: Subscribe to funding rate data and implement
        return 0.0

    def _project_funding_cost_1h(self) -> float:
        """Project funding cost for next 1 hour based on current positions."""
        funding_rate = self._get_current_funding_rate()
        long_inventory = self._calculate_inventory("long")
        short_inventory = self._calculate_inventory("short")

        # Funding is paid every 8h, so 1h projection = rate * (1/8) * inventory
        long_cost = funding_rate * (1 / 8) * long_inventory
        short_cost = -funding_rate * (1 / 8) * short_inventory  # Short receives funding

        return long_cost + short_cost

    def _get_realized_pnl(self) -> float:
        """
        Get total realized PnL.

        Note: This should track realized PnL from closed positions. Currently returns 0.0
        as placeholder. In production, implement based on strategy tracking or Nautilus statistics.
        """
        # TODO: Implement realized PnL tracking
        return 0.0

    def _get_unrealized_pnl(self) -> float:
        """Get total unrealized PnL from open positions."""
        if self._instrument is None or self._last_mid is None:
            return 0.0

        total_unrealized = 0.0

        for side in ["long", "short"]:
            position_id_str = f"{self.instrument_id}-{side.upper()}"
            position_id = PositionId(position_id_str)
            position = self.cache.position(position_id)

            if position and position.quantity > 0:
                # Calculate unrealized PnL based on current price
                current_price = Price(self._last_mid, precision=self._instrument.price_precision)
                unrealized = float(position.unrealized_pnl(current_price))
                total_unrealized += unrealized

        return total_unrealized

    def _get_total_pnl(self) -> float:
        """Total PnL = realized + unrealized."""
        return self._get_realized_pnl() + self._get_unrealized_pnl()

    def _get_uptime_seconds(self) -> float:
        """Get strategy uptime in seconds."""
        if self._start_time is None:
            return 0.0

        return (self.clock.timestamp_ns() - self._start_time) / 1e9

    def _log_diagnostic_status(self) -> None:
        """Log diagnostic information about fills and TP/SL attachments."""
        # Count open positions
        long_position_id = PositionId(f"{self.instrument_id}-LONG")
        short_position_id = PositionId(f"{self.instrument_id}-SHORT")
        long_pos = self.cache.position(long_position_id)
        short_pos = self.cache.position(short_position_id)

        long_qty = float(long_pos.quantity) if long_pos and long_pos.quantity > 0 else 0.0
        short_qty = float(short_pos.quantity) if short_pos and short_pos.quantity > 0 else 0.0

        # Count TP/SL orders
        tp_orders = 0
        sl_orders = 0
        for order in self.cache.orders_open(venue=self._venue):
            order_id = order.client_order_id.value
            if "-TP-" in order_id:
                tp_orders += 1
            elif "-SL-" in order_id:
                sl_orders += 1

        # Log comprehensive status
        self.log.info(
            f"[DIAGNOSTIC] Fills: {self._total_fills} total ({len(self._fills_with_exits)} with TP/SL), "
            f"Positions: LONG={long_qty:.3f} BTC, SHORT={short_qty:.3f} BTC, "
            f"Exit Orders: {tp_orders} TPs, {sl_orders} SLs, "
            f"Grid Orders: {len(self._get_live_grid_orders())} active, "
            f"Last Mid: {self._last_mid:.2f}"
        )

    def _cancel_side_orders(self, side: str) -> int:
        """Cancel all orders for given side."""
        cancelled = 0
        position_suffix = f"-{side.upper()}"

        open_orders = self.cache.orders_open(venue=self._venue)

        for order in open_orders:
            if not order.client_order_id.value.startswith(self._strategy_name):
                continue

            if position_suffix in str(order.client_order_id):
                if order.is_open:
                    self.cancel_order(order)
                    cancelled += 1

        self.log.warning(f"Cancelled {cancelled} {side} orders")
        return cancelled

    def _close_side_position(self, side: str) -> dict | None:
        """Submit market order to close position for given side."""
        if self._instrument is None:
            return None

        position_id_str = f"{self.instrument_id}-{side.upper()}"
        position_id = PositionId(position_id_str)
        position = self.cache.position(position_id)

        if position and position.quantity > 0:
            # Determine closing side
            close_side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY

            # Create market order
            order = self.order_factory.market(
                instrument_id=self._instrument.id,
                order_side=close_side,
                quantity=position.quantity,
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )

            self.submit_order(order, position_id=position_id)

            self.log.warning(f"Closing {side} position: {position.quantity} @ market")

            return {
                "side": side,
                "size": float(position.quantity),
                "order_id": str(order.client_order_id),
            }

        return None

    # =====================================================================
    # CRITICAL ERROR AND RISK MANAGEMENT METHODS
    # =====================================================================

    def _handle_critical_error(self) -> None:
        """
        Handle critical errors by entering safe mode.

        This method is called when a critical error occurs that could
        compromise trading safety. It cancels all orders and sets flags
        to prevent further trading.
        """
        self.log.critical("CRITICAL ERROR - Entering safe mode")

        # Set flags to stop trading
        self._critical_error = True
        self._pause_trading = True

        # Cancel all pending orders
        try:
            # Cancel grid orders
            for order in self.cache.orders_open(instrument_id=self.instrument_id):
                self.cancel_order(order)
                self.log.info(f"Cancelled order {order.client_order_id} due to critical error")
        except Exception as e:
            self.log.error(f"Failed to cancel orders during critical error handling: {e}")

        # Log state for debugging
        self.log.critical(
            "Critical error handler complete. " "Trading paused. Manual intervention required."
        )

    def _validate_order_size(self, order) -> bool:
        """
        Validate order size against account balance.

        Args:
            order: Order to validate

        Returns:
            True if order size is valid, False otherwise
        """
        try:
            account = self.portfolio.account(self._venue)
            if not account:
                self.log.error("No account found for position validation")
                return False

            # Calculate notional value
            notional = float(order.quantity) * float(order.price) if hasattr(order, "price") else 0

            # Get free balance (assuming USDT as base currency)
            from nautilus_trader.model.identifiers import Currency

            base_currency = Currency.from_str("USDT")
            free_balance = float(account.balance_free(base_currency))

            # Apply maximum position percentage (default 95%)
            max_position_pct = (
                getattr(self._hedge_grid_config, "max_position_pct", 0.95)
                if self._hedge_grid_config
                else 0.95
            )

            if notional > free_balance * max_position_pct:
                self.log.warning(
                    f"Order notional {notional:.2f} exceeds limit "
                    f"({free_balance:.2f} * {max_position_pct:.2%} = {free_balance * max_position_pct:.2f})"
                )
                return False

            return True

        except Exception as e:
            self.log.error(f"Error validating order size: {e}")
            return False  # Fail safe - reject if we can't validate

    def _check_circuit_breaker(self) -> None:
        """
        Check if circuit breaker should activate based on error rate.

        Monitors the error rate over a sliding window and activates
        the circuit breaker if too many errors occur.
        """
        if self._circuit_breaker_active:
            # Check if cooldown period has passed
            if (
                self._circuit_breaker_reset_time
                and self.clock.timestamp_ns() >= self._circuit_breaker_reset_time
            ):
                self._circuit_breaker_active = False
                self._circuit_breaker_reset_time = None
                self.log.info("Circuit breaker reset - resuming normal operation")
            return

        # Track current error
        now = self.clock.timestamp_ns()
        self._error_window.append(now)

        # Remove old errors outside 1-minute window
        one_minute_ago = now - 60_000_000_000  # 60 seconds in nanoseconds
        while self._error_window and self._error_window[0] < one_minute_ago:
            self._error_window.popleft()

        # Check threshold (default 10 errors per minute)
        max_errors = (
            getattr(self._hedge_grid_config, "max_errors_per_minute", 10)
            if self._hedge_grid_config
            else 10
        )

        if len(self._error_window) >= max_errors:
            self.log.critical(
                f"Circuit breaker activated - {len(self._error_window)} errors in last minute"
            )
            self._circuit_breaker_active = True

            # Cancel all orders
            for order in self.cache.orders_open(instrument_id=self.instrument_id):
                self.cancel_order(order)

            # Set reset time (5 minutes from now)
            cooldown_seconds = (
                getattr(self._hedge_grid_config, "circuit_breaker_cooldown_seconds", 300)
                if self._hedge_grid_config
                else 300
            )
            self._circuit_breaker_reset_time = now + (cooldown_seconds * 1_000_000_000)

            self.log.info(f"Circuit breaker will reset in {cooldown_seconds} seconds")

    def _check_drawdown_limit(self) -> None:
        """
        Check and enforce maximum drawdown limit.

        Monitors account balance and halts trading if drawdown
        exceeds configured threshold.
        """
        try:
            account = self.portfolio.account(self._venue)
            if not account:
                return

            # Get current balance
            from nautilus_trader.model.identifiers import Currency

            base_currency = Currency.from_str("USDT")
            current_balance = float(account.balance_total(base_currency))

            # Initialize peak balance if needed
            if self._initial_balance is None:
                self._initial_balance = current_balance
                self._peak_balance = current_balance
                return

            # Update peak balance
            self._peak_balance = max(current_balance, self._peak_balance)

            # Calculate drawdown percentage
            if self._peak_balance > 0:
                drawdown_pct = ((self._peak_balance - current_balance) / self._peak_balance) * 100

                # Check against limit (default 20%)
                max_drawdown_pct = (
                    getattr(self._hedge_grid_config, "max_drawdown_pct", 20.0)
                    if self._hedge_grid_config
                    else 20.0
                )

                if drawdown_pct > max_drawdown_pct and not self._drawdown_protection_triggered:
                    self.log.critical(
                        f"Max drawdown exceeded: {drawdown_pct:.2f}% > {max_drawdown_pct:.2f}% "
                        f"(peak: {self._peak_balance:.2f}, current: {current_balance:.2f})"
                    )

                    # Flatten all positions
                    self._flatten_all_positions()

                    # Set flags to prevent further trading
                    self._drawdown_protection_triggered = True
                    self._pause_trading = True

        except Exception as e:
            self.log.error(f"Error checking drawdown limit: {e}")

    def _flatten_all_positions(self) -> None:
        """
        Emergency close all positions at market.

        Used when critical risk thresholds are breached.
        """
        self.log.warning("EMERGENCY: Flattening all positions")

        # Cancel all pending orders first
        for order in self.cache.orders_open(instrument_id=self.instrument_id):
            try:
                self.cancel_order(order)
                self.log.info(f"Cancelled order {order.client_order_id}")
            except Exception as e:
                self.log.error(f"Failed to cancel order {order.client_order_id}: {e}")

        # Close LONG position
        long_position_info = self._close_side_position("long")
        if long_position_info:
            self.log.info(f"Closing LONG position: {long_position_info}")

        # Close SHORT position
        short_position_info = self._close_side_position("short")
        if short_position_info:
            self.log.info(f"Closing SHORT position: {short_position_info}")

        self.log.warning("All positions flattened")
