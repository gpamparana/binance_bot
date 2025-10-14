"""Tests for configuration loading and validation."""

import os
from pathlib import Path

import pytest

from naut_hedgegrid.config.backtest import BacktestConfigLoader
from naut_hedgegrid.config.base import ConfigError
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfigLoader


def test_venue_config_loads_successfully(tmp_path: Path) -> None:
    """Test loading a valid venue configuration."""
    os.environ["BINANCE_API_KEY"] = "test_key"
    os.environ["BINANCE_API_SECRET"] = "test_secret"

    config_file = tmp_path / "venue.yaml"
    config_file.write_text("""
venue:
  name: BINANCE
  venue_type: futures
  account_type: PERPETUAL_LINEAR

api:
  api_key: ${BINANCE_API_KEY}
  api_secret: ${BINANCE_API_SECRET}
  testnet: false

trading:
  hedge_mode: true
  leverage: 10
  margin_type: CROSSED

risk:
  max_leverage: 20
  min_order_size_usdt: 5.0
  max_order_size_usdt: 100000.0

precision:
  price_precision: 2
  quantity_precision: 3
  min_notional: 5.0

rate_limits:
  orders_per_second: 5
  orders_per_minute: 100
  weight_per_minute: 1200

websocket:
  ping_interval: 30
  reconnect_timeout: 60
  max_reconnect_attempts: 10
""")

    config = VenueConfigLoader.load(config_file)

    assert config.venue.name == "BINANCE"
    assert config.api.api_key == "test_key"
    assert config.trading.leverage == 10
    assert config.risk.max_leverage == 20

    # Cleanup
    del os.environ["BINANCE_API_KEY"]
    del os.environ["BINANCE_API_SECRET"]


def test_venue_config_missing_required_field(tmp_path: Path) -> None:
    """Test error when required field is missing."""
    config_file = tmp_path / "incomplete.yaml"
    config_file.write_text("""
venue:
  name: BINANCE
  venue_type: futures

# Missing api, trading, risk, precision, rate_limits, websocket
""")

    with pytest.raises(ConfigError, match="validation failed"):
        VenueConfigLoader.load(config_file)


def test_venue_config_invalid_field_type(tmp_path: Path) -> None:
    """Test error when field has wrong type."""
    config_file = tmp_path / "invalid_type.yaml"
    config_file.write_text("""
venue:
  name: BINANCE
  venue_type: futures
  account_type: PERPETUAL_LINEAR

api:
  api_key: test_key
  api_secret: test_secret
  testnet: false

trading:
  hedge_mode: true
  leverage: "not_a_number"  # Should be int
  margin_type: CROSSED

risk:
  max_leverage: 20
  min_order_size_usdt: 5.0
  max_order_size_usdt: 100000.0

precision:
  price_precision: 2
  quantity_precision: 3
  min_notional: 5.0

rate_limits:
  orders_per_second: 5
  orders_per_minute: 100
  weight_per_minute: 1200

websocket:
  ping_interval: 30
  reconnect_timeout: 60
  max_reconnect_attempts: 10
""")

    with pytest.raises(ConfigError, match="validation failed"):
        VenueConfigLoader.load(config_file)


def test_strategy_config_loads_successfully(tmp_path: Path) -> None:
    """Test loading a valid strategy configuration."""
    config_file = tmp_path / "strategy.yaml"
    config_file.write_text("""
strategy:
  name: hedge_grid_v1
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 25.0
  grid_levels_long: 10
  grid_levels_short: 10
  base_qty: 0.001
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 8

rebalance:
  recenter_trigger_bps: 150.0
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
""")

    config = HedgeGridConfigLoader.load(config_file)

    assert config.strategy.name == "hedge_grid_v1"
    assert config.grid.grid_step_bps == 25.0
    assert config.grid.grid_levels_long == 10
    assert config.exit.tp_steps == 2
    assert config.regime.adx_len == 14


def test_strategy_config_validation_constraints(tmp_path: Path) -> None:
    """Test strategy config field validation constraints."""
    config_file = tmp_path / "invalid_constraints.yaml"
    config_file.write_text("""
strategy:
  name: hedge_grid_v1
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: -10.0  # Should be > 0
  grid_levels_long: 10
  grid_levels_short: 10
  base_qty: 0.001
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 8

rebalance:
  recenter_trigger_bps: 150.0
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
""")

    with pytest.raises(ConfigError, match="validation failed"):
        HedgeGridConfigLoader.load(config_file)


def test_backtest_config_loads_successfully(tmp_path: Path) -> None:
    """Test loading a valid backtest configuration."""
    config_file = tmp_path / "backtest.yaml"
    config_file.write_text("""
backtest:
  name: test_backtest
  description: Test backtest

time_range:
  start_time: "2024-01-01T00:00:00Z"
  end_time: "2024-01-02T00:00:00Z"
  timezone: UTC

data:
  catalog_path: ./data/catalog
  instruments:
    - instrument_id: BTCUSDT-PERP.BINANCE
      data_types:
        - type: TradeTick
        - type: QuoteTick
  sources:
    - type: parquet
      path: ./data/binance
      glob_pattern: "*.parquet"

venues:
  - config_path: ./configs/venues/binance_futures.yaml
    starting_balances:
      - currency: USDT
        total: 10000.0
        locked: 0.0

strategies:
  - config_path: ./configs/strategies/hedge_grid_v1.yaml
    enabled: true

execution:
  latency:
    order_submit_ms: 50
    order_cancel_ms: 30
    fill_mean_ms: 100
    fill_std_ms: 20
  fill_model:
    type: realistic
    maker_fill_prob: 0.9
    aggressive_fill_prob: 1.0
    slippage_bps: 1.0
  fees:
    maker_bps: 2.0
    taker_bps: 5.0
    funding_apply: true

risk:
  max_drawdown_pct: 20.0
  max_daily_loss_pct: 5.0
  stop_on_liquidation: true

output:
  report_dir: ./reports
  save_trades: true
  save_positions: true
  save_account_state: true
  log_level: INFO

metrics:
  calculate:
    - sharpe_ratio
    - max_drawdown
  risk_free_rate: 0.04
  periods_per_year: 365
""")

    config = BacktestConfigLoader.load(config_file)

    assert config.backtest.name == "test_backtest"
    assert config.execution.fees.maker_bps == 2.0
    assert len(config.venues) == 1
    assert len(config.strategies) == 1


def test_config_helpful_error_messages(tmp_path: Path) -> None:
    """Test that validation errors provide helpful messages."""
    config_file = tmp_path / "multiple_errors.yaml"
    config_file.write_text("""
strategy:
  # Missing name field
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 25.0
  grid_levels_long: 200  # Exceeds max of 100
  grid_levels_short: 10
  base_qty: -0.001  # Should be > 0
  qty_scale: 1.1

exit:
  tp_steps: 2
  sl_steps: 8

rebalance:
  recenter_trigger_bps: 150.0
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
""")

    with pytest.raises(ConfigError) as exc_info:
        HedgeGridConfigLoader.load(config_file)

    error_msg = str(exc_info.value)
    # Should contain helpful information about multiple errors
    assert "validation failed" in error_msg.lower()
    assert "Field:" in error_msg
