"""Test operational controls integration with HedgeGridV1 strategy.

This module tests the integration of operational controls (metrics, kill switch, API)
with the HedgeGridV1 trading strategy.
"""

import threading
from unittest.mock import Mock

import pytest


class TestOpsIntegration:
    """Test suite for operational controls integration."""

    def test_strategy_has_operational_methods(self):
        """Test that strategy implements all required operational methods."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        # Create minimal config
        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Check all required methods exist
        assert hasattr(strategy, "get_operational_metrics")
        assert hasattr(strategy, "attach_kill_switch")
        assert hasattr(strategy, "flatten_side")
        assert hasattr(strategy, "set_throttle")
        assert hasattr(strategy, "get_ladders_snapshot")

        # Check helper methods exist
        assert hasattr(strategy, "_calculate_inventory")
        assert hasattr(strategy, "_calculate_net_inventory")
        assert hasattr(strategy, "_get_active_rungs")
        assert hasattr(strategy, "_get_margin_ratio")
        assert hasattr(strategy, "_calculate_maker_ratio")
        assert hasattr(strategy, "_get_current_funding_rate")
        assert hasattr(strategy, "_project_funding_cost_1h")
        assert hasattr(strategy, "_get_realized_pnl")
        assert hasattr(strategy, "_get_unrealized_pnl")
        assert hasattr(strategy, "_get_total_pnl")
        assert hasattr(strategy, "_get_uptime_seconds")
        assert hasattr(strategy, "_cancel_side_orders")
        assert hasattr(strategy, "_close_side_position")

    def test_ops_lock_exists(self):
        """Test that strategy has thread-safe ops lock."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        assert hasattr(strategy, "_ops_lock")
        assert isinstance(strategy._ops_lock, threading.Lock)

    def test_metrics_tracking_state(self):
        """Test that strategy initializes metrics tracking state."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Check metrics tracking state exists
        assert hasattr(strategy, "_start_time")
        assert hasattr(strategy, "_last_bar_time")
        assert hasattr(strategy, "_total_fills")
        assert hasattr(strategy, "_maker_fills")
        assert hasattr(strategy, "_throttle")
        assert hasattr(strategy, "_kill_switch")

        # Check initial values
        assert strategy._start_time is None  # Set in on_start()
        assert strategy._last_bar_time is None
        assert strategy._total_fills == 0
        assert strategy._maker_fills == 0
        assert strategy._throttle == 1.0
        assert strategy._kill_switch is None

    def test_ladder_state_tracking(self):
        """Test that strategy tracks ladder state for snapshots."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Check ladder state exists
        assert hasattr(strategy, "_last_long_ladder")
        assert hasattr(strategy, "_last_short_ladder")
        assert strategy._last_long_ladder is None
        assert strategy._last_short_ladder is None

    def test_set_throttle_validation(self):
        """Test that set_throttle validates input."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Valid throttle values
        strategy.set_throttle(0.0)
        assert strategy._throttle == 0.0

        strategy.set_throttle(1.0)
        assert strategy._throttle == 1.0

        strategy.set_throttle(0.5)
        assert strategy._throttle == 0.5

        # Invalid throttle values
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            strategy.set_throttle(-0.1)

        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            strategy.set_throttle(1.1)

    def test_get_ladders_snapshot_empty(self):
        """Test get_ladders_snapshot with no ladder data."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        snapshot = strategy.get_ladders_snapshot()

        assert "long_ladder" in snapshot
        assert "short_ladder" in snapshot
        assert "mid_price" in snapshot
        assert snapshot["long_ladder"] == []
        assert snapshot["short_ladder"] == []
        assert snapshot["mid_price"] == 0.0

    def test_attach_kill_switch(self):
        """Test attaching kill switch to strategy."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)
        kill_switch = Mock()

        strategy.attach_kill_switch(kill_switch)

        assert strategy._kill_switch is kill_switch

    def test_operational_metrics_returns_dict(self):
        """Test that get_operational_metrics returns proper dict structure."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Mock necessary attributes
        strategy._start_time = 1000000000000000000  # nanoseconds

        metrics = strategy.get_operational_metrics()

        # Check all required keys exist
        assert "long_inventory_usdt" in metrics
        assert "short_inventory_usdt" in metrics
        assert "net_inventory_usdt" in metrics
        assert "active_rungs_long" in metrics
        assert "active_rungs_short" in metrics
        assert "open_orders_count" in metrics
        assert "margin_ratio" in metrics
        assert "maker_ratio" in metrics
        assert "funding_rate_current" in metrics
        assert "funding_cost_1h_projected_usdt" in metrics
        assert "realized_pnl_usdt" in metrics
        assert "unrealized_pnl_usdt" in metrics
        assert "total_pnl_usdt" in metrics
        assert "uptime_seconds" in metrics
        assert "last_bar_timestamp" in metrics

        # Check all values are numeric
        for key, value in metrics.items():
            assert isinstance(value, (int, float)), f"{key} should be numeric, got {type(value)}"

    def test_flatten_side_validation(self):
        """Test flatten_side accepts valid side parameters."""
        from naut_hedgegrid.strategies.hedge_grid_v1.config import HedgeGridV1Config
        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1

        config = HedgeGridV1Config(
            instrument_id="BTCUSDT-PERP.BINANCE",
            bar_type="BTCUSDT-PERP.BINANCE-1-MINUTE-LAST",
            hedge_grid_config_path="configs/strategies/hedge_grid_v1.yaml",
        )

        strategy = HedgeGridV1(config)

        # Mock cache and venue
        strategy.cache = Mock()
        strategy.cache.orders_open = Mock(return_value=[])
        strategy.cache.position = Mock(return_value=None)

        # Test valid sides
        result = strategy.flatten_side("long")
        assert "cancelled_orders" in result
        assert "closing_positions" in result

        result = strategy.flatten_side("short")
        assert "cancelled_orders" in result
        assert "closing_positions" in result

        result = strategy.flatten_side("both")
        assert "cancelled_orders" in result
        assert "closing_positions" in result


class TestOperationsManager:
    """Test suite for OperationsManager."""

    def test_operations_manager_initialization(self):
        """Test OperationsManager initializes correctly."""
        from naut_hedgegrid.ops import OperationsManager

        strategy = Mock()
        ops_manager = OperationsManager(
            strategy=strategy,
            instrument_id="BTCUSDT-PERP.BINANCE",
            prometheus_port=9091,
            api_port=8081,
        )

        assert ops_manager.strategy is strategy
        assert ops_manager.instrument_id == "BTCUSDT-PERP.BINANCE"
        assert ops_manager.prometheus_port == 9091
        assert ops_manager.api_port == 8081
        assert not ops_manager.is_running

    def test_update_metrics_calls_strategy(self):
        """Test that update_metrics calls strategy.get_operational_metrics()."""
        from naut_hedgegrid.ops import OperationsManager

        strategy = Mock()
        strategy.get_operational_metrics = Mock(
            return_value={
                "long_inventory_usdt": 1000.0,
                "short_inventory_usdt": 500.0,
            }
        )

        ops_manager = OperationsManager(
            strategy=strategy,
            instrument_id="BTCUSDT-PERP.BINANCE",
        )

        # Start the manager (will start prometheus server)
        ops_manager.is_running = True  # Fake running state to skip server start

        # Update metrics
        ops_manager.update_metrics()

        # Verify strategy method was called
        strategy.get_operational_metrics.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
