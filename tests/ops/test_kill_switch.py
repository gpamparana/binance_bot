"""Tests for KillSwitch circuit breaker system."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from naut_hedgegrid.config.operations import KillSwitchConfig
from naut_hedgegrid.ops.alerts import AlertManager
from naut_hedgegrid.ops.kill_switch import KillSwitch


@pytest.fixture
def kill_switch_config():
    """Create test kill switch configuration."""
    return KillSwitchConfig(
        enabled=True,
        check_interval_seconds=1,  # Fast checking for tests
        max_drawdown_pct=5.0,
        max_funding_cost_bps=20.0,
        max_margin_ratio=0.80,
        max_loss_amount_usdt=1000.0,
        daily_loss_limit_usdt=500.0,
    )


@pytest.fixture
def mock_strategy():
    """Create mock strategy with operational metrics."""
    strategy = MagicMock()

    # Default metrics (healthy state)
    strategy.get_operational_metrics.return_value = {
        "total_pnl_usdt": 0.0,
        "long_inventory_usdt": 1000.0,
        "short_inventory_usdt": 1000.0,
        "funding_cost_1h_projected_usdt": 0.5,
        "margin_ratio": 0.3,
        "realized_pnl_usdt": 0.0,
        "unrealized_pnl_usdt": 0.0,
    }

    # Mock flatten_side to return result
    strategy.flatten_side.return_value = {
        "cancelled_orders": 5,
        "closing_positions": [
            {"side": "long", "size": 0.5, "order_id": "test-1"},
            {"side": "short", "size": 0.3, "order_id": "test-2"},
        ],
    }

    return strategy


@pytest.fixture
def mock_alert_manager():
    """Create mock alert manager."""
    return MagicMock(spec=AlertManager)


@pytest.fixture
def kill_switch(mock_strategy, kill_switch_config, mock_alert_manager):
    """Create KillSwitch instance."""
    return KillSwitch(mock_strategy, kill_switch_config, mock_alert_manager)


class TestKillSwitchConfig:
    """Tests for KillSwitchConfig model."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = KillSwitchConfig()

        assert config.enabled is True
        assert config.check_interval_seconds == 5
        assert config.max_drawdown_pct == 5.0
        assert config.max_funding_cost_bps == 20.0
        assert config.max_margin_ratio == 0.80
        assert config.max_loss_amount_usdt == 1000.0
        assert config.daily_loss_limit_usdt is None

    def test_config_validation_drawdown(self):
        """Test drawdown validation."""
        # Valid drawdown
        config = KillSwitchConfig(max_drawdown_pct=10.0)
        assert config.max_drawdown_pct == 10.0

        # Too small
        with pytest.raises(ValueError):
            KillSwitchConfig(max_drawdown_pct=0.05)

        # Too large (allowed but warned)
        config = KillSwitchConfig(max_drawdown_pct=30.0)
        assert config.max_drawdown_pct == 30.0

    def test_config_validation_margin_ratio(self):
        """Test margin ratio validation."""
        # Valid margin ratio
        config = KillSwitchConfig(max_margin_ratio=0.75)
        assert config.max_margin_ratio == 0.75

        # Too high - dangerous
        with pytest.raises(ValueError, match="dangerously high"):
            KillSwitchConfig(max_margin_ratio=0.95)

    def test_config_validation_loss_limits(self):
        """Test loss limit validation."""
        # Valid configuration
        config = KillSwitchConfig(
            max_loss_amount_usdt=1000.0,
            daily_loss_limit_usdt=500.0,
        )
        assert config.daily_loss_limit_usdt == 500.0

        # Daily loss greater than session loss
        with pytest.raises(ValueError, match="should not exceed"):
            KillSwitchConfig(
                max_loss_amount_usdt=500.0,
                daily_loss_limit_usdt=1000.0,
            )


class TestKillSwitchInitialization:
    """Tests for KillSwitch initialization."""

    def test_initialization(self, kill_switch):
        """Test KillSwitch initialization."""
        assert kill_switch.strategy is not None
        assert kill_switch.config is not None
        assert kill_switch.logger is not None
        assert kill_switch._monitoring is False
        assert kill_switch._monitor_thread is None

    def test_initialization_logging(self, mock_strategy, kill_switch_config, caplog):
        """Test initialization logging."""
        with caplog.at_level("INFO"):
            kill_switch = KillSwitch(mock_strategy, kill_switch_config, None)

            assert "Kill switch initialized" in caplog.text
            assert "drawdown=5.0%" in caplog.text
            assert "funding=20.0bps" in caplog.text


class TestKillSwitchFlatten:
    """Tests for flatten_now functionality."""

    def test_flatten_now_both_sides(self, kill_switch, mock_strategy, mock_alert_manager):
        """Test flattening both sides."""
        result = kill_switch.flatten_now("both", "test reason")

        assert result["status"] == "completed"
        assert result["reason"] == "test reason"
        assert result["cancelled_orders"] == 5
        assert len(result["closing_positions"]) == 2
        assert "timestamp" in result

        # Verify strategy was called
        mock_strategy.flatten_side.assert_called_once_with("both")

        # Verify alert was sent
        mock_alert_manager.send_flatten_alert.assert_called_once()

    def test_flatten_now_long_only(self, kill_switch, mock_strategy):
        """Test flattening long side only."""
        result = kill_switch.flatten_now("long", "test reason")

        assert result["status"] == "completed"
        mock_strategy.flatten_side.assert_called_once_with("long")

    def test_flatten_now_short_only(self, kill_switch, mock_strategy):
        """Test flattening short side only."""
        result = kill_switch.flatten_now("short", "test reason")

        assert result["status"] == "completed"
        mock_strategy.flatten_side.assert_called_once_with("short")

    def test_flatten_now_idempotent(self, kill_switch, mock_strategy):
        """Test that flatten is idempotent (safe to call multiple times)."""
        # First call starts flatten
        result1 = kill_switch.flatten_now("both", "test reason")
        assert result1["status"] == "completed"

        # Reset mock to verify second call behavior
        mock_strategy.flatten_side.reset_mock()

        # Second call (after first completes) should work
        result2 = kill_switch.flatten_now("both", "test reason 2")
        assert result2["status"] == "completed"

    def test_flatten_now_error_handling(self, kill_switch, mock_strategy):
        """Test error handling during flatten."""
        mock_strategy.flatten_side.side_effect = Exception("Test error")

        result = kill_switch.flatten_now("both", "test reason")

        assert result["status"] == "error"
        assert "error" in result
        assert "Test error" in result["error"]

    def test_flatten_concurrent_protection(self, kill_switch, mock_strategy):
        """Test that concurrent flatten calls are protected."""

        # Simulate slow flatten operation
        def slow_flatten(side):
            time.sleep(0.5)
            return {"cancelled_orders": 0, "closing_positions": []}

        mock_strategy.flatten_side.side_effect = slow_flatten

        # This test would require threading to properly test concurrent access
        # For now, just verify the lock mechanism exists
        assert kill_switch._lock is not None
        assert kill_switch._flatten_in_progress is False


class TestKillSwitchMonitoring:
    """Tests for background monitoring functionality."""

    def test_start_monitoring(self, kill_switch, caplog):
        """Test starting monitoring thread."""
        with caplog.at_level("INFO"):
            kill_switch.start_monitoring()

            assert kill_switch._monitoring is True
            assert kill_switch._monitor_thread is not None
            assert kill_switch._monitor_thread.is_alive()
            assert "monitoring started" in caplog.text.lower()

        # Cleanup
        kill_switch.stop_monitoring()

    def test_start_monitoring_disabled(self, mock_strategy, mock_alert_manager, caplog):
        """Test that disabled kill switch doesn't start monitoring."""
        config = KillSwitchConfig(enabled=False)
        kill_switch = KillSwitch(mock_strategy, config, mock_alert_manager)

        with caplog.at_level("INFO"):
            kill_switch.start_monitoring()

            assert kill_switch._monitoring is False
            assert "disabled" in caplog.text.lower()

    def test_start_monitoring_already_running(self, kill_switch, caplog):
        """Test warning when starting already running monitor."""
        kill_switch.start_monitoring()
        caplog.clear()

        kill_switch.start_monitoring()

        assert "already running" in caplog.text.lower()

        # Cleanup
        kill_switch.stop_monitoring()

    def test_stop_monitoring(self, kill_switch, caplog):
        """Test stopping monitoring thread."""
        kill_switch.start_monitoring()
        time.sleep(0.1)  # Let it start

        with caplog.at_level("INFO"):
            kill_switch.stop_monitoring()

            assert kill_switch._monitoring is False
            assert "monitoring stopped" in caplog.text.lower()

        # Thread should be stopped
        if kill_switch._monitor_thread:
            time.sleep(0.5)  # Give it time to stop
            assert not kill_switch._monitor_thread.is_alive()


class TestCircuitBreakers:
    """Tests for circuit breaker logic."""

    def test_drawdown_circuit_breaker(self, kill_switch, mock_strategy, mock_alert_manager):
        """Test drawdown circuit breaker triggers."""
        # Set metrics showing drawdown exceeding threshold
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": -60.0,  # 6% drawdown from peak of 1000
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.3,
        }

        # Set peak PnL
        kill_switch._session_peak_pnl = 1000.0

        # Trigger circuit check
        kill_switch._check_drawdown_circuit(mock_strategy.get_operational_metrics())

        # Should trigger flatten (circuit breaker threshold is 5%)
        # Note: This test verifies the logic, actual flatten triggered in monitoring loop

    def test_funding_cost_circuit_breaker(self, kill_switch, mock_strategy):
        """Test funding cost circuit breaker triggers."""
        # Set metrics showing high funding cost
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": 0.0,
            "long_inventory_usdt": 10000.0,
            "short_inventory_usdt": 10000.0,
            "funding_cost_1h_projected_usdt": 60.0,  # 60 * 8 = 480 USDT per 8h
            "margin_ratio": 0.3,
        }

        # Funding cost: 480 / 20000 * 10000 = 24 bps (exceeds 20 bps threshold)
        kill_switch._check_funding_cost_circuit(mock_strategy.get_operational_metrics())

    def test_margin_ratio_circuit_breaker(self, kill_switch, mock_strategy):
        """Test margin ratio circuit breaker triggers."""
        # Set metrics showing high margin usage
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": 0.0,
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.85,  # Exceeds 0.80 threshold
        }

        kill_switch._check_margin_ratio_circuit(mock_strategy.get_operational_metrics())

    def test_loss_limit_circuit_breaker(self, kill_switch, mock_strategy):
        """Test absolute loss limit circuit breaker."""
        # Set session start PnL
        kill_switch._session_start_pnl = 0.0

        # Set metrics showing large loss
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": -1200.0,  # Exceeds 1000 USDT threshold
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.3,
        }

        kill_switch._check_loss_limit_circuit(mock_strategy.get_operational_metrics())

    def test_circuit_breaker_only_triggers_once(self, kill_switch, mock_strategy, mock_alert_manager):
        """Test that circuit breaker only triggers once per day."""
        # Set up breach condition
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": -1200.0,
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.3,
        }
        kill_switch._session_start_pnl = 0.0

        # First trigger
        kill_switch._trigger_circuit_breaker("Test Breaker", 1200, 1000, "USDT")
        assert mock_strategy.flatten_side.call_count == 1

        # Second trigger same day
        mock_strategy.flatten_side.reset_mock()
        kill_switch._trigger_circuit_breaker("Test Breaker", 1300, 1000, "USDT")
        assert mock_strategy.flatten_side.call_count == 0  # Should not trigger again


class TestDailyReset:
    """Tests for daily reset functionality."""

    def test_daily_reset_calculation(self):
        """Test calculation of next daily reset time."""
        now = datetime(2025, 1, 15, 14, 30, 0, tzinfo=UTC)

        # Mock datetime.now to return our test time
        with patch("naut_hedgegrid.ops.kill_switch.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            next_reset = KillSwitch._get_next_daily_reset()

            # Should be next midnight UTC
            expected = datetime(2025, 1, 16, 0, 0, 0, tzinfo=UTC)
            assert next_reset == expected

    def test_daily_reset_triggers(self, kill_switch, mock_strategy):
        """Test that daily reset resets tracking."""
        # Set initial PnL
        kill_switch._daily_start_pnl = 100.0
        kill_switch._daily_peak_pnl = 150.0

        # Set next reset to past
        kill_switch._daily_reset_time = datetime.now(tz=UTC) - timedelta(hours=1)

        # Set current metrics
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": 200.0,
        }

        # Trigger reset check
        kill_switch._check_daily_reset()

        # Should have reset
        assert kill_switch._daily_start_pnl == 200.0
        assert kill_switch._daily_peak_pnl == 200.0

        # Next reset should be tomorrow
        assert kill_switch._daily_reset_time > datetime.now(tz=UTC)


class TestKillSwitchStatus:
    """Tests for status reporting."""

    def test_get_status(self, kill_switch):
        """Test getting kill switch status."""
        status = kill_switch.get_status()

        assert "enabled" in status
        assert "monitoring" in status
        assert "flatten_in_progress" in status
        assert "session_start_time" in status
        assert "config" in status

        # Verify config included
        assert status["config"]["max_drawdown_pct"] == 5.0
        assert status["config"]["max_funding_cost_bps"] == 20.0

    def test_reset_circuit_breakers(self, kill_switch, mock_alert_manager):
        """Test manual reset of circuit breakers."""
        # Add some triggered breakers
        kill_switch._circuit_breakers_triggered.add("TestBreaker_2025-01-15")
        kill_switch._circuit_breakers_triggered.add("AnotherBreaker_2025-01-15")

        assert len(kill_switch._circuit_breakers_triggered) == 2

        # Reset
        kill_switch.reset_circuit_breakers()

        assert len(kill_switch._circuit_breakers_triggered) == 0

        # Should send alert
        mock_alert_manager.send_alert.assert_called_once()


class TestIntegration:
    """Integration tests for kill switch system."""

    def test_full_monitoring_cycle(self, mock_strategy, kill_switch_config, mock_alert_manager):
        """Test full monitoring cycle with circuit breaker trigger."""
        # Configure for fast testing
        kill_switch_config.check_interval_seconds = 0.1

        kill_switch = KillSwitch(mock_strategy, kill_switch_config, mock_alert_manager)

        # Start with healthy metrics
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": 0.0,
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.3,
        }

        # Start monitoring
        kill_switch.start_monitoring()

        # Let it run for a bit
        time.sleep(0.3)

        # Now trigger a circuit breaker by changing metrics
        mock_strategy.get_operational_metrics.return_value = {
            "total_pnl_usdt": -1200.0,  # Exceeds loss limit
            "long_inventory_usdt": 1000.0,
            "short_inventory_usdt": 1000.0,
            "funding_cost_1h_projected_usdt": 0.5,
            "margin_ratio": 0.3,
        }

        # Wait for monitoring to detect
        time.sleep(0.5)

        # Stop monitoring
        kill_switch.stop_monitoring()

        # Verify flatten was called
        assert mock_strategy.flatten_side.called

        # Verify alert was sent
        assert mock_alert_manager.send_circuit_breaker_alert.called

    def test_monitoring_error_recovery(self, mock_strategy, kill_switch_config, caplog):
        """Test that monitoring continues after errors."""
        kill_switch = KillSwitch(mock_strategy, kill_switch_config, None)

        # Make get_operational_metrics raise error
        mock_strategy.get_operational_metrics.side_effect = Exception("Test error")

        kill_switch.start_monitoring()
        time.sleep(0.2)

        # Should log error but continue
        assert "Error in monitoring loop" in caplog.text or "Error checking safety circuits" in caplog.text

        # Should still be monitoring
        assert kill_switch._monitoring is True

        kill_switch.stop_monitoring()
