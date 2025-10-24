"""Tests for constraints validator."""

import pytest

from naut_hedgegrid.metrics.report import PerformanceMetrics
from naut_hedgegrid.optimization.constraints import ConstraintsValidator, ConstraintThresholds


class TestConstraintThresholds:
    """Tests for ConstraintThresholds."""

    def test_default_thresholds(self):
        """Test default threshold values."""
        thresholds = ConstraintThresholds()

        assert thresholds.min_sharpe_ratio == 1.0
        assert thresholds.max_drawdown_pct == 20.0
        assert thresholds.min_trades == 50
        assert thresholds.min_win_rate_pct == 45.0
        assert thresholds.min_profit_factor == 1.1
        assert thresholds.min_calmar_ratio == 0.5

    def test_custom_thresholds(self):
        """Test custom threshold values."""
        thresholds = ConstraintThresholds(
            min_sharpe_ratio=1.5,
            max_drawdown_pct=15.0,
            min_trades=100,
            min_win_rate_pct=50.0,
            min_profit_factor=1.5,
            min_calmar_ratio=1.0
        )

        assert thresholds.min_sharpe_ratio == 1.5
        assert thresholds.max_drawdown_pct == 15.0
        assert thresholds.min_trades == 100
        assert thresholds.min_win_rate_pct == 50.0
        assert thresholds.min_profit_factor == 1.5
        assert thresholds.min_calmar_ratio == 1.0

    def test_invalid_drawdown_threshold(self):
        """Test that invalid drawdown raises error."""
        with pytest.raises(ValueError, match="Max drawdown"):
            ConstraintThresholds(max_drawdown_pct=150.0)

        with pytest.raises(ValueError, match="Max drawdown"):
            ConstraintThresholds(max_drawdown_pct=-5.0)

    def test_invalid_trades_threshold(self):
        """Test that negative trades raises error."""
        with pytest.raises(ValueError, match="Minimum trades"):
            ConstraintThresholds(min_trades=-10)

    def test_invalid_win_rate_threshold(self):
        """Test that invalid win rate raises error."""
        with pytest.raises(ValueError, match="Min win rate"):
            ConstraintThresholds(min_win_rate_pct=150.0)


class TestConstraintsValidator:
    """Tests for ConstraintsValidator."""

    def test_initialization(self):
        """Test validator initialization."""
        validator = ConstraintsValidator()

        assert validator.thresholds is not None
        assert validator.strict_mode is True

    def test_custom_thresholds(self):
        """Test validator with custom thresholds."""
        custom_thresholds = ConstraintThresholds(min_sharpe_ratio=2.0)
        validator = ConstraintsValidator(thresholds=custom_thresholds)

        assert validator.thresholds.min_sharpe_ratio == 2.0

    def test_is_valid_passing_metrics(self):
        """Test validation passes for good metrics."""
        validator = ConstraintsValidator()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,  # > 1.0
            sortino_ratio=3.0,
            calmar_ratio=2.0,  # > 0.5
            max_drawdown_pct=10.0,  # < 20.0
            max_drawdown_duration_days=10,
            total_trades=100,  # > 50
            winning_trades=60,
            losing_trades=40,
            win_rate_pct=60.0,  # > 45.0
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=2.0,  # > 1.1
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        assert validator.is_valid(metrics) is True

    def test_is_valid_failing_sharpe(self):
        """Test validation fails for low Sharpe ratio."""
        validator = ConstraintsValidator()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=0.5,  # < 1.0 - FAIL
            sortino_ratio=1.0,
            calmar_ratio=2.0,
            max_drawdown_pct=10.0,
            max_drawdown_duration_days=10,
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate_pct=60.0,
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=2.0,
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        assert validator.is_valid(metrics) is False

    def test_is_valid_failing_drawdown(self):
        """Test validation fails for excessive drawdown."""
        validator = ConstraintsValidator()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=25.0,  # > 20.0 - FAIL
            max_drawdown_duration_days=50,
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate_pct=60.0,
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=2.0,
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        assert validator.is_valid(metrics) is False

    def test_is_valid_failing_trades(self):
        """Test validation fails for too few trades."""
        validator = ConstraintsValidator()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=10.0,
            max_drawdown_duration_days=10,
            total_trades=30,  # < 50 - FAIL
            winning_trades=20,
            losing_trades=10,
            win_rate_pct=66.7,
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=2.0,
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        assert validator.is_valid(metrics) is False

    def test_get_violations(self):
        """Test retrieval of constraint violations."""
        validator = ConstraintsValidator()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=0.5,  # FAIL
            sortino_ratio=1.0,
            calmar_ratio=0.3,  # FAIL
            max_drawdown_pct=25.0,  # FAIL
            max_drawdown_duration_days=50,
            total_trades=30,  # FAIL
            winning_trades=15,
            losing_trades=15,
            win_rate_pct=50.0,
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=1.0,  # FAIL
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        violations = validator.get_violations(metrics)

        # Should have multiple violations
        assert len(violations) > 0
        assert any("Sharpe" in v for v in violations)
        assert any("drawdown" in v for v in violations)
        assert any("Trade count" in v for v in violations)

    def test_lenient_mode(self):
        """Test lenient mode allows some violations."""
        validator = ConstraintsValidator(strict_mode=False)

        # Metrics with one violation
        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=0.9,  # Slightly below threshold - MINOR FAIL
            sortino_ratio=2.0,
            calmar_ratio=2.0,
            max_drawdown_pct=10.0,
            max_drawdown_duration_days=10,
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate_pct=60.0,
            avg_win=20.0,
            avg_loss=-10.0,
            profit_factor=2.0,
            avg_trade_pnl=10.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=50.0,
            funding_paid=10.0,
            funding_received=15.0,
            net_funding_pnl=5.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
        )

        # Lenient mode should pass with 1 violation
        assert validator.is_valid(metrics) is True

    def test_update_thresholds(self):
        """Test dynamic threshold updates."""
        validator = ConstraintsValidator()

        original_sharpe = validator.thresholds.min_sharpe_ratio

        validator.update_thresholds(min_sharpe_ratio=2.0)

        assert validator.thresholds.min_sharpe_ratio == 2.0
        assert validator.thresholds.min_sharpe_ratio != original_sharpe

    def test_update_thresholds_invalid_parameter(self):
        """Test that invalid parameter updates raise error."""
        validator = ConstraintsValidator()

        with pytest.raises(ValueError, match="Unknown threshold parameter"):
            validator.update_thresholds(invalid_param=123)