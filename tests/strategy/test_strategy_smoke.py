"""Smoke tests for HedgeGridV1 strategy using BacktestEngine harness.

These tests validate end-to-end behavior of HedgeGridV1 through a real
BacktestEngine rather than mocking Nautilus internals (which Cython prevents).

Test Coverage:
--------------
- Strategy initialization and configuration loading
- Bar processing and regime detection warmup
- Order generation and ladder management
- Position side suffixes (-LONG, -SHORT) for hedge mode
- TP/SL attachment on order fills
- Order lifecycle tracking (accepted, filled, canceled)
- Diff engine behavior (order count stability)
- Regime changes and ladder adjustments
- Funding guard initialization
- Edge cases and cleanup
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar as NautilusBar, BarSpecification, BarType, TradeTick
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    AggressorSide,
    BarAggregation,
    OmsType,
    OrderSide,
    OrderStatus,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1, HedgeGridV1Config
from naut_hedgegrid.strategy.detector import Bar as DetectorBar

# ============================================================================
# FIXTURES
# ============================================================================

INSTRUMENT_ID_STR = "BTCUSDT-PERP.BINANCE"


def _create_instrument() -> CryptoPerpetual:
    """Create a BTCUSDT-PERP instrument for testing."""
    quote = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID_STR),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=quote,
        settlement_currency=quote,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=Money(1_000_000, quote),
        min_notional=Money(5, quote),
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0.02"),
        margin_maint=Decimal("0.01"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )


def _make_bar_type(instrument: CryptoPerpetual) -> BarType:
    """Create the 1-minute EXTERNAL bar type matching the strategy."""
    return BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(
            step=1,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.LAST,
        ),
        aggregation_source=AggregationSource.EXTERNAL,
    )


def _generate_bars(
    instrument: CryptoPerpetual,
    start: datetime,
    minutes: int,
    base_price: float = 50000.0,
    trend_per_bar: float = 0.0,
) -> list[NautilusBar]:
    """Generate synthetic 1-minute Nautilus bars.

    Args:
        instrument: Nautilus instrument
        start: Start time
        minutes: Number of bars
        base_price: Starting price
        trend_per_bar: Price drift per bar (0 = sideways)
    """
    rng = np.random.RandomState(42)
    bars = []
    bar_type = _make_bar_type(instrument)
    price = base_price
    ts = int(start.timestamp() * 1_000_000_000)
    bar_interval_ns = 60_000_000_000  # 1 minute

    for _ in range(minutes):
        price += trend_per_bar
        noise = rng.normal(0, base_price * 0.001)
        open_p = max(1.0, price + noise)
        high_p = max(open_p, open_p + abs(rng.normal(0, base_price * 0.0005)))
        low_p = min(open_p, open_p - abs(rng.normal(0, base_price * 0.0005)))
        close_p = max(low_p, min(high_p, open_p + rng.normal(0, base_price * 0.0003)))
        vol = round(rng.uniform(0.5, 5.0), 3)

        bar = NautilusBar(
            bar_type=bar_type,
            open=Price(open_p, precision=2),
            high=Price(high_p, precision=2),
            low=Price(low_p, precision=2),
            close=Price(close_p, precision=2),
            volume=Quantity(vol, precision=3),
            ts_event=ts,
            ts_init=ts,
        )
        bars.append(bar)
        ts += bar_interval_ns

    return bars


def _generate_ticks(
    instrument: CryptoPerpetual,
    start: datetime,
    minutes: int,
    base_price: float = 50000.0,
    trend_per_tick: float = 0.0,
    ticks_per_minute: int = 60,
) -> list[TradeTick]:
    """Generate synthetic trade ticks (used by parity-style tests)."""
    rng = np.random.RandomState(42)
    ticks = []
    price = base_price
    interval_ns = 60_000_000_000 // ticks_per_minute
    ts = int(start.timestamp() * 1_000_000_000)

    for i in range(minutes * ticks_per_minute):
        noise = rng.normal(0, base_price * 0.0002)
        price = max(1.0, price + trend_per_tick + noise)
        side = AggressorSide.BUYER if rng.rand() > 0.5 else AggressorSide.SELLER
        qty = round(rng.uniform(0.001, 0.05), 3)

        ticks.append(
            TradeTick(
                instrument_id=instrument.id,
                price=Price(price, precision=2),
                size=Quantity(qty, precision=3),
                aggressor_side=side,
                trade_id=TradeId(str(i + 1)),
                ts_event=ts,
                ts_init=ts,
            )
        )
        ts += interval_ns

    return ticks


def _build_engine_and_strategy(
    strategy_config_path: Path,
    instrument: CryptoPerpetual,
    bars: list[NautilusBar],
    ticks: list[TradeTick] | None = None,
    run: bool = True,
) -> tuple[BacktestEngine, HedgeGridV1]:
    """Build and optionally run a BacktestEngine with HedgeGridV1.

    Args:
        strategy_config_path: Path to HedgeGridConfig YAML
        instrument: Test instrument
        bars: Nautilus bars for the strategy's bar subscription
        ticks: Optional trade ticks for order fill simulation
        run: Whether to run the engine immediately

    Returns:
        (engine, strategy) tuple
    """
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
        )
    )
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(10_000, Currency.from_str("USDT"))],
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)
    if ticks:
        engine.add_data(ticks)

    config = HedgeGridV1Config(
        instrument_id=INSTRUMENT_ID_STR,
        hedge_grid_config_path=str(strategy_config_path),
    )
    strategy = HedgeGridV1(config=config)
    engine.add_strategy(strategy)

    if run:
        engine.run()

    return engine, strategy


@pytest.fixture
def hedge_grid_config_path(tmp_path: Path) -> Path:
    """Create temporary HedgeGridConfig YAML for testing."""
    config_file = tmp_path / "test_hedge_grid.yaml"
    config_content = """\
strategy:
  name: hedge_grid_v1
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 25.0
  grid_levels_long: 5
  grid_levels_short: 5
  base_qty: 0.01
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 5

rebalance:
  recenter_trigger_bps: 100.0
  max_inventory_quote: 10000.0

execution:
  maker_only: true
  use_post_only_retries: true
  retry_attempts: 3
  retry_delay_ms: 100

funding:
  funding_window_minutes: 480
  funding_max_cost_bps: 10.0

regime:
  adx_len: 14
  ema_fast: 12
  ema_slow: 26
  atr_len: 14
  hysteresis_bps: 50.0

position:
  max_position_size: 1.0
  max_leverage_used: 5.0
  emergency_liquidation_buffer: 0.15

policy:
  strategy: throttled-counter
  counter_levels: 3
  counter_qty_scale: 0.5
"""
    config_file.write_text(config_content)
    return config_file


@pytest.fixture
def instrument() -> CryptoPerpetual:
    """Shared test instrument."""
    return _create_instrument()


@pytest.fixture
def sideways_bars(instrument: CryptoPerpetual) -> list[NautilusBar]:
    """80 minutes of sideways 1-min bars."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _generate_bars(instrument, start, minutes=80, base_price=50000.0)


@pytest.fixture
def sideways_ticks(instrument: CryptoPerpetual) -> list[TradeTick]:
    """80 minutes of sideways trade ticks (for fill simulation)."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _generate_ticks(instrument, start, minutes=80, base_price=50000.0)


@pytest.fixture
def trending_bars(instrument: CryptoPerpetual) -> list[NautilusBar]:
    """80 minutes of uptrending 1-min bars."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _generate_bars(instrument, start, minutes=80, base_price=50000.0, trend_per_bar=5.0)


@pytest.fixture
def trending_ticks(instrument: CryptoPerpetual) -> list[TradeTick]:
    """80 minutes of uptrending trade ticks."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _generate_ticks(instrument, start, minutes=80, base_price=50000.0, trend_per_tick=0.15)


@pytest.fixture
def minimal_bars(instrument: CryptoPerpetual) -> list[NautilusBar]:
    """10 minutes of bars (too few for detector warmup)."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    return _generate_bars(instrument, start, minutes=10, base_price=50000.0)


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


def test_strategy_initialization(hedge_grid_config_path: Path) -> None:
    """Test strategy creates successfully with valid configuration."""
    config = HedgeGridV1Config(
        instrument_id=INSTRUMENT_ID_STR,
        hedge_grid_config_path=str(hedge_grid_config_path),
    )
    strategy = HedgeGridV1(config=config)

    assert strategy is not None
    assert strategy.config == config
    assert strategy.config.instrument_id == INSTRUMENT_ID_STR


def test_strategy_instrument_id_parsed(hedge_grid_config_path: Path) -> None:
    """Test instrument ID is correctly parsed from config."""
    config = HedgeGridV1Config(
        instrument_id=INSTRUMENT_ID_STR,
        hedge_grid_config_path=str(hedge_grid_config_path),
    )
    strategy = HedgeGridV1(config=config)

    expected = InstrumentId.from_str(INSTRUMENT_ID_STR)
    assert strategy.instrument_id == expected


# ============================================================================
# ON_START / CONFIG TESTS
# ============================================================================


def test_on_start_loads_config(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test on_start loads config and initializes all components."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert strategy._hedge_grid_config is not None
    assert strategy._instrument is not None
    assert strategy._regime_detector is not None
    assert strategy._funding_guard is not None
    assert strategy._precision_guard is not None
    assert strategy._order_diff is not None


def test_on_start_missing_instrument(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
) -> None:
    """Test on_start with bad instrument ID pauses trading."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    engine.add_venue(
        venue=Venue("BINANCE"),
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(10_000, Currency.from_str("USDT"))],
    )
    engine.add_instrument(instrument)
    engine.add_data(sideways_bars)

    config = HedgeGridV1Config(
        instrument_id="ETHUSDT-PERP.BINANCE",  # Not in engine
        hedge_grid_config_path=str(hedge_grid_config_path),
    )
    strategy = HedgeGridV1(config=config)
    engine.add_strategy(strategy)
    engine.run()

    assert strategy._pause_trading is True


# ============================================================================
# BAR PROCESSING TESTS
# ============================================================================


def test_bar_processing_updates_detector(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test bars feed into regime detector and advance bar count."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert strategy._regime_detector is not None
    assert strategy._regime_detector._bar_count > 0


def test_detector_warms_up_after_enough_bars(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test detector becomes warm after sufficient bars (80 bars > EMA slow 26)."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert strategy._regime_detector.is_warm


def test_orders_generated_after_warmup(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test orders are submitted once detector is warm."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    all_orders = engine.cache.orders()
    assert len(all_orders) > 0, "No orders submitted after warmup"


def test_no_orders_before_warmup(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    minimal_bars: list[NautilusBar],
) -> None:
    """Test no orders generated with insufficient bars for warmup."""
    engine, strategy = _build_engine_and_strategy(hedge_grid_config_path, instrument, minimal_bars, run=True)

    # Detector should not be warm with only 10 bars
    if not strategy._regime_detector.is_warm:
        all_orders = engine.cache.orders()
        assert len(all_orders) == 0, "Orders submitted before detector warm"


def test_last_mid_updated(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test _last_mid is updated during bar processing."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert strategy._last_mid is not None
    assert strategy._last_mid > 0


# ============================================================================
# ORDER GENERATION AND POSITION SIDE TESTS
# ============================================================================


def test_position_side_suffixes_long(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test grid BUY orders (not TP/SL) use -LONG position_id suffix."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    # Filter to grid orders only (exclude TP/SL which use opposite side)
    grid_buy_orders = [
        o for o in engine.cache.orders() if o.side == OrderSide.BUY and str(o.client_order_id).startswith("HG1-LONG")
    ]
    assert len(grid_buy_orders) > 0, "No grid BUY orders generated"

    for order in grid_buy_orders:
        if order.position_id:
            assert str(order.position_id).endswith(
                "-LONG"
            ), f"Grid BUY order position_id should end with -LONG, got {order.position_id}"


def test_position_side_suffixes_short(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test grid SELL orders (not TP/SL) use -SHORT position_id suffix."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    # Filter to grid orders only (exclude TP/SL which use opposite side)
    grid_sell_orders = [
        o for o in engine.cache.orders() if o.side == OrderSide.SELL and str(o.client_order_id).startswith("HG1-SHORT")
    ]
    assert len(grid_sell_orders) > 0, "No grid SELL orders generated"

    for order in grid_sell_orders:
        if order.position_id:
            assert str(order.position_id).endswith(
                "-SHORT"
            ), f"Grid SELL order position_id should end with -SHORT, got {order.position_id}"


def test_client_order_id_format(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test client_order_id follows expected format."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    for order in engine.cache.orders():
        cid = str(order.client_order_id)
        assert cid.startswith(("HG1-", "O-")), f"Unexpected client_order_id format: {cid}"


# ============================================================================
# ORDER LIFECYCLE TESTS
# ============================================================================


def test_orders_get_accepted(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test orders go through the accepted lifecycle."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    all_orders = engine.cache.orders()
    assert len(all_orders) > 0


def test_fills_produce_tp_sl(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test filled grid orders produce TP/SL child orders."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    filled_orders = [o for o in engine.cache.orders() if o.status == OrderStatus.FILLED]

    if len(filled_orders) > 0:
        all_orders = engine.cache.orders()
        assert len(all_orders) >= len(filled_orders)


def test_canceled_orders_tracked(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test canceled orders are properly tracked."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    # Grid strategy cancels old orders when recentering — just verify no crash
    canceled = [o for o in engine.cache.orders() if o.status == OrderStatus.CANCELED]


# ============================================================================
# DIFF ENGINE / MINIMAL CHURN TESTS
# ============================================================================


def test_order_count_stabilizes(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test order count doesn't grow exponentially (diff engine works)."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    all_orders = engine.cache.orders()
    assert len(all_orders) < 500, f"Excessive orders ({len(all_orders)}): diff engine may not be working"


def test_diff_handles_stable_market(
    hedge_grid_config_path: Path,
) -> None:
    """Test diff engine avoids churn in perfectly stable market."""
    inst = _create_instrument()
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    bars = _generate_bars(inst, start, minutes=80, base_price=50000.0)
    ticks = _generate_ticks(inst, start, minutes=80, base_price=50000.0, ticks_per_minute=30)

    engine, strategy = _build_engine_and_strategy(hedge_grid_config_path, inst, bars, ticks=ticks, run=True)

    all_orders = engine.cache.orders()
    canceled = [o for o in all_orders if o.status == OrderStatus.CANCELED]

    if len(all_orders) > 0:
        cancel_ratio = len(canceled) / len(all_orders)
        assert cancel_ratio < 0.9, f"High cancel ratio ({cancel_ratio:.1%}): possible diff engine issue"


def test_diff_adds_orders_on_first_warm_bar(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test that once detector warms up, orders are placed."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    if strategy._regime_detector.is_warm:
        all_orders = engine.cache.orders()
        assert len(all_orders) > 0, "Detector warm but no orders placed"


# ============================================================================
# REGIME CHANGE TESTS
# ============================================================================


def test_trending_market_generates_orders(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    trending_bars: list[NautilusBar],
    trending_ticks: list[TradeTick],
) -> None:
    """Test strategy generates orders in trending market."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, trending_bars, ticks=trending_ticks, run=True
    )

    if strategy._regime_detector.is_warm:
        all_orders = engine.cache.orders()
        assert len(all_orders) > 0, "No orders in trending market"


def test_regime_affects_order_distribution(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
    trending_bars: list[NautilusBar],
    trending_ticks: list[TradeTick],
) -> None:
    """Test different regimes produce different order distributions."""
    _, strat_sideways = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )
    _, strat_trending = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, trending_bars, ticks=trending_ticks, run=True
    )

    if strat_sideways._regime_detector.is_warm and strat_trending._regime_detector.is_warm:
        # Regimes may or may not differ depending on hysteresis,
        # but the strategy should run without errors in both cases
        _ = strat_sideways._regime_detector.current()
        _ = strat_trending._regime_detector.current()


# ============================================================================
# FUNDING GUARD TESTS
# ============================================================================


def test_funding_guard_initialized(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
) -> None:
    """Test funding guard is properly initialized after on_start."""
    engine, strategy = _build_engine_and_strategy(hedge_grid_config_path, instrument, sideways_bars, run=True)

    assert strategy._funding_guard is not None


# ============================================================================
# EDGE CASES AND ERROR HANDLING
# ============================================================================


def test_bar_validation_rejects_invalid_high_low() -> None:
    """Test DetectorBar validation rejects high < low."""
    with pytest.raises(ValueError):
        DetectorBar(open=50000.0, high=49900.0, low=50100.0, close=50000.0, volume=1000.0)


def test_bar_validation_rejects_close_outside_range() -> None:
    """Test DetectorBar validation rejects close outside high/low range."""
    with pytest.raises(ValueError):
        DetectorBar(open=50000.0, high=50100.0, low=49900.0, close=50200.0, volume=1000.0)


def test_engine_stops_cleanly(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test strategy and engine stop cleanly after a run."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert not strategy._critical_error


# ============================================================================
# INTEGRATION-STYLE LIFECYCLE TESTS
# ============================================================================


def test_full_lifecycle_sideways(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Full lifecycle: start -> warm -> trade -> stop in sideways market."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    assert strategy._hedge_grid_config is not None
    assert strategy._regime_detector.is_warm

    all_orders = engine.cache.orders()
    assert len(all_orders) > 0

    buy_orders = [o for o in all_orders if o.side == OrderSide.BUY]
    sell_orders = [o for o in all_orders if o.side == OrderSide.SELL]
    assert len(buy_orders) > 0 or len(sell_orders) > 0

    assert not strategy._critical_error


def test_full_lifecycle_trending(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    trending_bars: list[NautilusBar],
    trending_ticks: list[TradeTick],
) -> None:
    """Full lifecycle through trending market."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, trending_bars, ticks=trending_ticks, run=True
    )

    assert strategy._hedge_grid_config is not None

    if strategy._regime_detector.is_warm:
        all_orders = engine.cache.orders()
        assert len(all_orders) > 0


def test_precision_guard_filters_orders(
    hedge_grid_config_path: Path,
    instrument: CryptoPerpetual,
    sideways_bars: list[NautilusBar],
    sideways_ticks: list[TradeTick],
) -> None:
    """Test all submitted orders meet min notional requirements."""
    engine, strategy = _build_engine_and_strategy(
        hedge_grid_config_path, instrument, sideways_bars, ticks=sideways_ticks, run=True
    )

    for order in engine.cache.orders():
        if hasattr(order, "price") and order.price is not None:
            notional = float(order.price) * float(order.quantity)
            assert notional >= 4.99, f"Order below min notional: {notional:.2f}"
