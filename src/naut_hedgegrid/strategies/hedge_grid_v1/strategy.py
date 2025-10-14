"""HedgeGridV1 trading strategy implementation."""

import threading
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    PositionSide,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.events import OrderAccepted, OrderCanceled, OrderFilled
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
from naut_hedgegrid.strategy.order_sync import LiveOrder, OrderDiff
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
        self.bar_type = BarType.from_str(config.bar_type)
        self.config_path = config.hedge_grid_config_path

        # State tracking
        self._hedge_grid_config: HedgeGridConfig | None = None
        self._instrument: Instrument | None = None
        self._precision_guard: PrecisionGuard | None = None
        self._regime_detector: RegimeDetector | None = None
        self._funding_guard: FundingGuard | None = None
        self._order_diff: OrderDiff | None = None
        self._last_mid: float | None = None
        self._grid_center: float = 0.0

        # Strategy identifier for order IDs
        self._strategy_name = "HG1"

        # Venue for order queries
        self._venue = Venue("BINANCE")

        # Operational controls state
        self._kill_switch = None
        self._throttle: float = 1.0  # Default to full aggressiveness
        self._ops_lock = threading.Lock()  # Thread-safe access to operational metrics

        # Metrics tracking
        self._start_time: int | None = None
        self._last_bar_time: datetime | None = None
        self._total_fills: int = 0
        self._maker_fills: int = 0

        # Ladder state for snapshot access
        self._last_long_ladder: Ladder | None = None
        self._last_short_ladder: Ladder | None = None

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

        # Create precision guard
        self._precision_guard = PrecisionGuard(instrument=self._instrument)
        self.log.info(
            f"Precision guard initialized: "
            f"tick={self._precision_guard.precision.price_tick}, "
            f"step={self._precision_guard.precision.qty_step}, "
            f"min_notional={self._precision_guard.precision.min_notional}"
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

        self.log.info("HedgeGridV1 strategy started successfully")

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
        if self._hedge_grid_config is None or self._regime_detector is None:
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

        # Log regime and price
        self.log.info(
            f"Bar: close={mid:.2f}, regime={regime}, warm={self._regime_detector.is_warm}"
        )

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
            mid=mid,
            cfg=self._hedge_grid_config,
            regime=regime,
        )

        self.log.info(
            f"Built {len(ladders)} ladder(s): "
            + ", ".join(f"{ladder.side}({len(ladder)} rungs)" for ladder in ladders)
        )

        # Store ladder state for snapshot access (before any filtering)
        with self._ops_lock:
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

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle order filled event.

        When a grid order fills:
        1. Parse client_order_id to get original rung metadata
        2. Retrieve rung TP/SL prices from grid calculation
        3. Create TP limit order (reduce-only) at tp_price
        4. Create SL stop-market order (reduce-only) at sl_price
        5. Submit both with correct position_id suffix

        Args:
            event: Order filled event

        """
        self.log.info(f"Order filled: {event.client_order_id} @ {event.last_px}")

        # Track fill statistics for metrics
        with self._ops_lock:
            self._total_fills += 1
            if event.liquidity_side == LiquiditySide.MAKER:
                self._maker_fills += 1

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
            f"Creating TP/SL for {side} fill @ {fill_price:.2f}: "
            f"TP={tp_price:.2f}, SL={sl_price:.2f}"
        )

        # Create position_id with side suffix
        position_id = PositionId(f"{self.instrument_id}-{side.value}")

        # Create TP limit order (reduce-only)
        tp_order = self._create_tp_order(
            side=side,
            quantity=fill_qty,
            tp_price=tp_price,
            position_id=position_id,
            level=level,  # type: ignore[arg-type]
        )

        # Create SL stop-market order (reduce-only)
        sl_order = self._create_sl_order(
            side=side,
            quantity=fill_qty,
            sl_price=sl_price,
            position_id=position_id,
            level=level,  # type: ignore[arg-type]
        )

        # Submit orders
        self.submit_order(tp_order, position_id=position_id)
        self.submit_order(sl_order, position_id=position_id)

        self.log.info(f"Submitted TP/SL orders for level {level}")

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """
        Handle order accepted event.

        Logs order acceptance. Order tracking is done via cache queries.

        Args:
            event: Order accepted event

        """
        # Log order acceptance
        if event.client_order_id.value.startswith(self._strategy_name):
            self.log.debug(f"Order accepted: {event.client_order_id}")

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """
        Handle order canceled event.

        Logs order cancellation. Order tracking is done via cache queries.

        Args:
            event: Order canceled event

        """
        # Log order cancellation
        if event.client_order_id.value.startswith(self._strategy_name):
            self.log.debug(f"Order canceled: {event.client_order_id}")

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
        order = self.cache.order(intent.client_order_id)
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
        Execute add operation (create new limit order).

        Args:
            intent: Create intent with order parameters

        """
        if self._instrument is None or intent.side is None:
            self.log.warning("Cannot create order: instrument or side missing")
            return

        # Create limit order
        order = self._create_limit_order(intent, self._instrument)

        # Create position_id with side suffix
        position_id = PositionId(f"{self.instrument_id}-{intent.side.value}")

        # Submit order
        self.submit_order(order, position_id=position_id)
        self.log.debug(f"Created order: {intent.client_order_id} @ {intent.price}")

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

        # Create order
        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=order_side,
            quantity=Quantity(intent.qty, precision=instrument.size_precision),
            price=Price(intent.price, precision=instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            post_only=True,  # Maker-only orders
            client_order_id=self.clock.generate_client_order_id(intent.client_order_id),
        )

        return order

    def _create_tp_order(
        self,
        side: Side,
        quantity: float,
        tp_price: float,
        position_id: PositionId,
        level: int,
    ) -> LimitOrder:
        """
        Create take-profit limit order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            tp_price: Take profit price
            position_id: Position ID with side suffix
            level: Grid level for order ID

        Returns:
            Reduce-only limit order at TP price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # TP order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate client_order_id for TP
        client_order_id_str = (
            f"{self._strategy_name}-TP-{side.value}-{level:02d}-{self.clock.timestamp_ns()}"
        )

        order = self.order_factory.limit(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            price=Price(tp_price, precision=self._instrument.price_precision),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=self.clock.generate_client_order_id(client_order_id_str),
        )

        return order

    def _create_sl_order(
        self,
        side: Side,
        quantity: float,
        sl_price: float,
        position_id: PositionId,
        level: int,
    ) -> StopMarketOrder:
        """
        Create stop-loss stop-market order (reduce-only).

        Args:
            side: Original fill side (LONG or SHORT)
            quantity: Position quantity to close
            sl_price: Stop loss trigger price
            position_id: Position ID with side suffix
            level: Grid level for order ID

        Returns:
            Reduce-only stop-market order at SL price

        """
        if self._instrument is None:
            raise RuntimeError("Instrument not initialized")

        # SL order is opposite side (close position)
        order_side = OrderSide.SELL if side == Side.LONG else OrderSide.BUY

        # Generate client_order_id for SL
        client_order_id_str = (
            f"{self._strategy_name}-SL-{side.value}-{level:02d}-{self.clock.timestamp_ns()}"
        )

        order = self.order_factory.stop_market(
            instrument_id=self._instrument.id,
            order_side=order_side,
            quantity=Quantity(quantity, precision=self._instrument.size_precision),
            trigger_price=Price(sl_price, precision=self._instrument.price_precision),
            trigger_type=TriggerType.LAST_TRADE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            client_order_id=self.clock.generate_client_order_id(client_order_id_str),
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

    @lru_cache(maxsize=1000)
    def _parse_cached_order_id(self, client_order_id: str) -> dict:
        """
        Parse client order ID with caching to avoid repeated parsing.

        Args:
            client_order_id: Client order ID string

        Returns:
            Parsed order ID dict

        """
        return parse_client_order_id(client_order_id)

    def _get_live_grid_orders(self) -> list[LiveOrder]:
        """
        Get live grid orders from cache.

        Returns:
            List of LiveOrder objects for grid orders only (not TP/SL)

        """
        live_orders = []

        # Query open orders from cache
        open_orders = self.cache.orders_open(venue=self._venue)

        for order in open_orders:
            # Only include grid orders (not TP/SL)
            if not order.client_order_id.value.startswith(self._strategy_name):
                continue

            # Skip TP/SL orders
            if "-TP-" in order.client_order_id.value or "-SL-" in order.client_order_id.value:
                continue

            # Parse order metadata
            try:
                parsed = self._parse_cached_order_id(order.client_order_id.value)
                side = parsed["side"]

                # Create LiveOrder object
                live_order = LiveOrder(
                    client_order_id=order.client_order_id.value,
                    side=side,  # type: ignore[arg-type]
                    price=float(order.price) if hasattr(order, "price") else 0.0,
                    qty=float(order.quantity),
                    status="OPEN",
                )
                live_orders.append(live_order)
            except (ValueError, KeyError) as e:
                self.log.warning(f"Could not parse order {order.client_order_id}: {e}")
                continue

        return live_orders

    # =====================================================================
    # OPERATIONAL CONTROLS INTEGRATION
    # =====================================================================

    def get_operational_metrics(self) -> dict:
        """
        Return current operational metrics for monitoring.

        Called periodically by OperationsManager to update Prometheus gauges.
        All metric access is thread-safe via _ops_lock.

        Returns:
            Dictionary containing operational metrics for Prometheus export

        """
        with self._ops_lock:
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
                "last_bar_timestamp": (
                    self._last_bar_time.timestamp() if self._last_bar_time else 0.0
                ),
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
        positions for the specified side(s). Thread-safe and can be called from
        kill switch background threads.

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

        with self._ops_lock:
            for s in sides:
                # Cancel orders
                cancelled = self._cancel_side_orders(s)
                result["cancelled_orders"] += cancelled

                # Close position
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

        with self._ops_lock:
            self._throttle = throttle

        self.log.info(f"Throttle set to {throttle:.2f}")

    def get_ladders_snapshot(self) -> dict:
        """
        Return current grid ladder state for API.

        Returns:
            Dictionary with current ladder state

        """
        with self._ops_lock:
            if self._last_long_ladder is None and self._last_short_ladder is None:
                return {"long_ladder": [], "short_ladder": [], "mid_price": 0.0}

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

        if position and not position.is_flat():
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

            if position and not position.is_flat():
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

        if position and not position.is_flat():
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
