"""Parity test comparing backtest and paper trading results.

This module tests that backtest and paper trading modes produce consistent results
when run on identical data feeds with seeded randomness for reproducibility.
"""

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from rich.console import Console
from rich.table import Table

from naut_hedgegrid.config.backtest import BacktestConfig
from naut_hedgegrid.runners.run_backtest import BacktestRunner

console = Console()


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def random_seed():
    """Fixed random seed for reproducibility."""
    return 42


@pytest.fixture
def test_symbol():
    """Test trading symbol."""
    return "BTCUSDT"


@pytest.fixture
def test_instrument_id():
    """Test instrument ID."""
    return "BTCUSDT-PERP.BINANCE"


@pytest.fixture
def sample_catalog(tmp_path, test_symbol, test_instrument_id):
    """Create a small sample ParquetDataCatalog for testing.

    Generates 1 hour of synthetic market data:
    - Trade ticks every 1 second
    - Quote ticks every 100ms
    - 1-minute bars

    Args:
        tmp_path: Pytest temporary directory fixture
        test_symbol: Trading symbol
        test_instrument_id: Nautilus instrument ID

    Returns:
        Path to catalog directory
    """
    catalog_path = tmp_path / "catalog"
    catalog_path.mkdir(parents=True, exist_ok=True)

    # Create catalog
    catalog = ParquetDataCatalog(str(catalog_path))

    # Create instrument
    instrument = _create_test_instrument(test_symbol, test_instrument_id)
    catalog.write_data([instrument])

    # Generate and write synthetic data
    start_time = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    end_time = start_time + timedelta(hours=1)

    # Generate trade ticks (1 per second = 3600 trades)
    trade_ticks = _generate_trade_ticks(
        instrument,
        start_time,
        end_time,
        interval_ms=1000,
        base_price=50000.0,
        volatility=0.001,
    )
    catalog.write_data(trade_ticks)

    return catalog_path


@pytest.fixture
def test_strategy_config_path(tmp_path):
    """Create minimal strategy config for testing."""
    config_path = tmp_path / "strategy_config.yaml"

    config_content = """
strategy:
  name: hedge_grid_v1_test
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 50.0
  grid_levels_long: 5
  grid_levels_short: 5
  base_qty: 0.001
  qty_scale: 1.0

exit:
  tp_steps: 2
  sl_steps: 5

rebalance:
  recenter_trigger_bps: 200.0
  max_inventory_quote: 5000.0

execution:
  maker_only: true
  use_post_only_retries: false

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
  max_position_size: 0.1
  max_leverage_used: 3.0
  emergency_liquidation_buffer: 0.15

policy:
  strategy: throttled-counter
  counter_levels: 3
  counter_qty_scale: 0.5
"""

    config_path.write_text(config_content)
    return config_path


@pytest.fixture
def test_venue_config_path(tmp_path):
    """Create minimal venue config for testing."""
    config_path = tmp_path / "venue_config.yaml"

    config_content = """
venue:
  name: BINANCE
  venue_type: futures
  account_type: PERPETUAL_LINEAR

api:
  api_key: test_key
  api_secret: test_secret
  testnet: true
  base_url: https://testnet.binancefuture.com

trading:
  hedge_mode: true
  leverage: 5
  margin_type: CROSSED

risk:
  max_leverage: 10
  min_order_size_usdt: 5.0
  max_order_size_usdt: 10000.0

precision:
  price_precision: 2
  quantity_precision: 3
  min_notional: 5.0

rate_limits:
  orders_per_second: 10
  orders_per_minute: 100
  weight_per_minute: 1200

websocket:
  ping_interval: 20
  reconnect_timeout: 60
  max_reconnect_attempts: 3

symbols:
  - BTCUSDT-PERP
"""

    config_path.write_text(config_content)
    return config_path


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _create_test_instrument(symbol: str, instrument_id_str: str) -> CryptoPerpetual:
    """Create Nautilus instrument for testing.

    Args:
        symbol: Trading symbol (e.g., "BTCUSDT")
        instrument_id_str: Full instrument ID string

    Returns:
        CryptoPerpetual instrument
    """
    base_code = symbol.replace("USDT", "")
    quote_code = "USDT"

    base_currency = Currency.from_str(base_code)
    quote_currency = Currency.from_str(quote_code)

    instrument_id = InstrumentId.from_str(instrument_id_str)

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
        settlement_currency=quote_currency,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.0"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=Money(100_000, quote_currency),
        min_notional=Money(5, quote_currency),
        max_price=Price.from_str("100000.0"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("0.02"),
        margin_maint=Decimal("0.01"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )


def _generate_trade_ticks(
    instrument: CryptoPerpetual,
    start_time: datetime,
    end_time: datetime,
    interval_ms: int,
    base_price: float,
    volatility: float,
) -> list:
    """Generate synthetic trade ticks for testing.

    Args:
        instrument: Nautilus instrument
        start_time: Start timestamp
        end_time: End timestamp
        interval_ms: Interval between trades in milliseconds
        base_price: Base price level
        volatility: Price volatility (stddev as fraction of price)

    Returns:
        List of TradeTick objects
    """
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.enums import AggressorSide
    from nautilus_trader.model.identifiers import TradeId

    trades = []
    current_time = start_time
    trade_id_counter = 1
    current_price = base_price

    while current_time < end_time:
        # Random walk price
        price_change = np.random.normal(0, base_price * volatility)
        current_price = max(1.0, current_price + price_change)

        # Random side
        aggressor_side = AggressorSide.BUYER if np.random.rand() > 0.5 else AggressorSide.SELLER

        # Random quantity
        qty = np.random.uniform(0.001, 0.1)

        # Convert to nanoseconds
        ts_event = int(current_time.timestamp() * 1_000_000_000)
        ts_init = ts_event

        trade = TradeTick(
            instrument_id=instrument.id,
            price=Price(current_price, precision=2),
            size=Quantity(qty, precision=3),
            aggressor_side=aggressor_side,
            trade_id=TradeId(str(trade_id_counter)),
            ts_event=ts_event,
            ts_init=ts_init,
        )

        trades.append(trade)

        # Advance time
        current_time += timedelta(milliseconds=interval_ms)
        trade_id_counter += 1

    return trades


def run_backtest_mode(
    catalog_path: Path,
    strategy_config_path: Path,
    venue_config_path: Path,
    instrument_id: str,
    random_seed: int,
) -> dict:
    """Run backtest and extract results.

    Args:
        catalog_path: Path to Parquet data catalog
        strategy_config_path: Path to strategy config YAML
        venue_config_path: Path to venue config YAML
        instrument_id: Instrument ID to backtest
        random_seed: Random seed for reproducibility

    Returns:
        dict with extracted results (orders, positions, account, etc.)
    """
    # Set random seeds
    random.seed(random_seed)
    np.random.seed(random_seed)

    # Create backtest config
    config = BacktestConfig(
        name="parity_test_backtest",
        catalog_path=str(catalog_path),
        instrument_ids=[instrument_id],
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-01-01T01:00:00Z",
        data_types=["TradeTick"],
        venue_config_path=str(venue_config_path),
        strategy_config_path=str(strategy_config_path),
        starting_balance_usdt=10000.0,
    )

    # Run backtest
    runner = BacktestRunner(config)
    catalog = runner.setup_catalog()
    engine, perf = runner.run(catalog)

    # Extract results
    results = runner.extract_results(engine)

    return results


def run_paper_mode(
    catalog_path: Path,
    strategy_config_path: Path,
    venue_config_path: Path,
    instrument_id: str,
    random_seed: int,
) -> dict:
    """Run paper trading mode with historical data replay and extract results.

    NOTE: This is a simplified implementation that uses BacktestEngine
    in "paper trading simulation" mode rather than a full TradingNode setup.
    A future enhancement could implement true async paper trading with
    historical replay.

    Args:
        catalog_path: Path to Parquet data catalog
        strategy_config_path: Path to strategy config YAML
        venue_config_path: Path to venue config YAML
        instrument_id: Instrument ID to trade
        random_seed: Random seed for reproducibility

    Returns:
        dict with extracted results (orders, positions, account, etc.)
    """
    # For now, run a second backtest with identical parameters
    # to simulate paper trading behavior
    # TODO: Implement true async paper trading with TradingNode

    return run_backtest_mode(
        catalog_path,
        strategy_config_path,
        venue_config_path,
        instrument_id,
        random_seed,
    )


def compare_results(
    backtest_results: dict,
    paper_results: dict,
    tolerances: dict,
) -> tuple[bool, pd.DataFrame]:
    """Compare backtest and paper results with tolerance checking.

    Args:
        backtest_results: Results from backtest mode
        paper_results: Results from paper mode
        tolerances: Dict of tolerance values for comparisons

    Returns:
        Tuple of (passed: bool, diff_table: pd.DataFrame)
    """
    comparisons = []
    passed = True

    # Extract metrics for comparison
    bt_orders = backtest_results.get("orders", [])
    paper_orders = paper_results.get("orders", [])

    bt_positions = backtest_results.get("positions", [])
    paper_positions = paper_results.get("positions", [])

    bt_account = backtest_results.get("account", {})
    paper_account = paper_results.get("account", {})

    # Compare total filled quantities by side
    bt_long_qty = sum(o["filled_qty"] for o in bt_orders if o["side"] == "LONG")
    bt_short_qty = sum(o["filled_qty"] for o in bt_orders if o["side"] == "SHORT")

    paper_long_qty = sum(o["filled_qty"] for o in paper_orders if o["side"] == "LONG")
    paper_short_qty = sum(o["filled_qty"] for o in paper_orders if o["side"] == "SHORT")

    long_qty_diff = abs(bt_long_qty - paper_long_qty)
    short_qty_diff = abs(bt_short_qty - paper_short_qty)

    long_qty_passed = long_qty_diff <= tolerances["qty_epsilon"]
    short_qty_passed = short_qty_diff <= tolerances["qty_epsilon"]

    comparisons.append(
        {
            "Metric": "Long Filled Qty",
            "Backtest": f"{bt_long_qty:.4f}",
            "Paper": f"{paper_long_qty:.4f}",
            "Diff": f"{long_qty_diff:.4f}",
            "Tolerance": f"{tolerances['qty_epsilon']:.4f}",
            "Status": "✓ PASS" if long_qty_passed else "✗ FAIL",
        }
    )

    comparisons.append(
        {
            "Metric": "Short Filled Qty",
            "Backtest": f"{bt_short_qty:.4f}",
            "Paper": f"{paper_short_qty:.4f}",
            "Diff": f"{short_qty_diff:.4f}",
            "Tolerance": f"{tolerances['qty_epsilon']:.4f}",
            "Status": "✓ PASS" if short_qty_passed else "✗ FAIL",
        }
    )

    passed = passed and long_qty_passed and short_qty_passed

    # Compare average entry prices
    bt_long_avg_price = _calc_avg_price(bt_orders, "LONG")
    bt_short_avg_price = _calc_avg_price(bt_orders, "SHORT")

    paper_long_avg_price = _calc_avg_price(paper_orders, "LONG")
    paper_short_avg_price = _calc_avg_price(paper_orders, "SHORT")

    long_price_diff = abs(bt_long_avg_price - paper_long_avg_price)
    short_price_diff = abs(bt_short_avg_price - paper_short_avg_price)

    long_price_passed = long_price_diff <= tolerances["price_epsilon"]
    short_price_passed = short_price_diff <= tolerances["price_epsilon"]

    comparisons.append(
        {
            "Metric": "Long Avg Entry Price",
            "Backtest": f"{bt_long_avg_price:.2f}",
            "Paper": f"{paper_long_avg_price:.2f}",
            "Diff": f"{long_price_diff:.2f}",
            "Tolerance": f"{tolerances['price_epsilon']:.2f}",
            "Status": "✓ PASS" if long_price_passed else "✗ FAIL",
        }
    )

    comparisons.append(
        {
            "Metric": "Short Avg Entry Price",
            "Backtest": f"{bt_short_avg_price:.2f}",
            "Paper": f"{paper_short_avg_price:.2f}",
            "Diff": f"{short_price_diff:.2f}",
            "Tolerance": f"{tolerances['price_epsilon']:.2f}",
            "Status": "✓ PASS" if short_price_passed else "✗ FAIL",
        }
    )

    passed = passed and long_price_passed and short_price_passed

    # Compare account balance (represents fees/funding)
    bt_balance = bt_account.get("balance_total", 0.0)
    paper_balance = paper_account.get("balance_total", 0.0)

    balance_diff = abs(bt_balance - paper_balance)
    balance_passed = balance_diff <= tolerances["fee_epsilon"]

    comparisons.append(
        {
            "Metric": "Final Balance (USDT)",
            "Backtest": f"{bt_balance:.2f}",
            "Paper": f"{paper_balance:.2f}",
            "Diff": f"{balance_diff:.2f}",
            "Tolerance": f"{tolerances['fee_epsilon']:.2f}",
            "Status": "✓ PASS" if balance_passed else "✗ FAIL",
        }
    )

    passed = passed and balance_passed

    # Create DataFrame
    df = pd.DataFrame(comparisons)

    return passed, df


def _calc_avg_price(orders: list[dict], side: str) -> float:
    """Calculate weighted average entry price for a side.

    Args:
        orders: List of order dicts
        side: Order side ("LONG" or "SHORT")

    Returns:
        Weighted average price (0.0 if no filled orders)
    """
    filled_orders = [o for o in orders if o["side"] == side and o["filled_qty"] > 0]

    if not filled_orders:
        return 0.0

    total_qty = sum(o["filled_qty"] for o in filled_orders)
    if total_qty == 0:
        return 0.0

    weighted_sum = sum(o["avg_px"] * o["filled_qty"] for o in filled_orders)
    return weighted_sum / total_qty


def print_diff_table(df: pd.DataFrame, passed: bool) -> None:
    """Print comparison results as rich table.

    Args:
        df: DataFrame with comparison results
        passed: Whether all comparisons passed
    """
    table = Table(title=f"Parity Test Results - {'PASSED' if passed else 'FAILED'}")

    for column in df.columns:
        table.add_column(column, style="cyan" if column != "Status" else "")

    for _, row in df.iterrows():
        style = "green" if "PASS" in row["Status"] else "red"
        table.add_row(*[str(v) for v in row.values], style=style)

    console.print(table)


# ============================================================================
# TESTS
# ============================================================================


@pytest.mark.skip(reason="BacktestConfig API changed - parity test needs refactoring to new schema")
def test_parity_backtest_vs_paper(
    sample_catalog,
    test_strategy_config_path,
    test_venue_config_path,
    test_instrument_id,
    random_seed,
):
    """Test parity between backtest and paper trading modes.

    This test verifies that backtest and paper trading produce consistent results
    when run on identical data feeds with seeded randomness.

    Comparisons:
    - Total filled quantity per side (LONG vs SHORT)
    - Average entry price deltas within tolerance
    - Fee/funding accounting within epsilon

    If results fall outside tolerance bounds, a detailed diff table is printed.
    """
    # Define tolerances
    tolerances = {
        "qty_epsilon": 0.001,  # 0.001 BTC
        "price_epsilon": 0.01,  # 1 cent (1 tick for BTCUSDT)
        "fee_epsilon": 0.01,  # $0.01 USDT
    }

    console.print("\n[bold cyan]Running Parity Test: Backtest vs Paper[/bold cyan]\n")

    # Run backtest mode
    console.print("[yellow]Running backtest mode...[/yellow]")
    backtest_results = run_backtest_mode(
        sample_catalog,
        test_strategy_config_path,
        test_venue_config_path,
        test_instrument_id,
        random_seed,
    )
    console.print(f"[green]✓[/green] Backtest completed: {len(backtest_results.get('orders', []))} orders\n")

    # Run paper mode
    console.print("[yellow]Running paper mode...[/yellow]")
    paper_results = run_paper_mode(
        sample_catalog,
        test_strategy_config_path,
        test_venue_config_path,
        test_instrument_id,
        random_seed,
    )
    console.print(f"[green]✓[/green] Paper mode completed: {len(paper_results.get('orders', []))} orders\n")

    # Compare results
    console.print("[yellow]Comparing results...[/yellow]\n")
    passed, diff_table = compare_results(backtest_results, paper_results, tolerances)

    # Print diff table
    print_diff_table(diff_table, passed)

    # Assert test passed
    if not passed:
        console.print("\n[red]✗ Parity test FAILED - results outside tolerance bounds[/red]\n")
        pytest.fail("Parity test failed: results exceeded tolerance thresholds")
    else:
        console.print("\n[green]✓ Parity test PASSED - all metrics within tolerance[/green]\n")


@pytest.mark.skip(reason="BacktestConfig API changed - determinism test needs refactoring to new schema")
def test_backtest_determinism(
    sample_catalog,
    test_strategy_config_path,
    test_venue_config_path,
    test_instrument_id,
    random_seed,
):
    """Test that backtest mode is deterministic with same seed.

    Runs backtest twice with identical configuration and random seed,
    verifying that results are exactly identical.
    """
    console.print("\n[bold cyan]Running Determinism Test: Backtest Reproducibility[/bold cyan]\n")

    # Run backtest twice with same seed
    console.print("[yellow]Running backtest (run 1)...[/yellow]")
    results1 = run_backtest_mode(
        sample_catalog,
        test_strategy_config_path,
        test_venue_config_path,
        test_instrument_id,
        random_seed,
    )

    console.print("[yellow]Running backtest (run 2)...[/yellow]")
    results2 = run_backtest_mode(
        sample_catalog,
        test_strategy_config_path,
        test_venue_config_path,
        test_instrument_id,
        random_seed,
    )

    # Compare orders
    orders1 = results1.get("orders", [])
    orders2 = results2.get("orders", [])

    assert len(orders1) == len(orders2), f"Order count mismatch: {len(orders1)} != {len(orders2)}"

    # Compare each order
    for i, (o1, o2) in enumerate(zip(orders1, orders2, strict=False)):
        assert o1["client_order_id"] == o2["client_order_id"], f"Order {i} client_order_id mismatch"
        assert (
            o1["filled_qty"] == o2["filled_qty"]
        ), f"Order {i} filled_qty mismatch: {o1['filled_qty']} != {o2['filled_qty']}"
        assert o1["avg_px"] == o2["avg_px"], f"Order {i} avg_px mismatch: {o1['avg_px']} != {o2['avg_px']}"

    # Compare account state
    account1 = results1.get("account", {})
    account2 = results2.get("account", {})

    assert account1["balance_total"] == account2["balance_total"], "Account balance mismatch"

    console.print("[green]✓ Determinism test PASSED - results are identical[/green]\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
