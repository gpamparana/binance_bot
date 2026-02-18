"""Tests for multi-objective function."""

import math

import pytest

from naut_hedgegrid.metrics.report import PerformanceMetrics
from naut_hedgegrid.optimization.objective import (
    MultiObjectiveFunction,
    NormalizationBounds,
    ObjectiveWeights,
)


class TestNormalizationBounds:
    """Tests for NormalizationBounds."""

    def test_normalize_value(self):
        """Test normalization of values."""
        bounds = NormalizationBounds(min_val=0.0, max_val=10.0)

        assert bounds.normalize(0.0) == 0.0
        assert bounds.normalize(10.0) == 1.0
        assert bounds.normalize(5.0) == 0.5

    def test_normalize_clipping(self):
        """Test that values outside bounds are clipped."""
        bounds = NormalizationBounds(min_val=0.0, max_val=10.0)

        # Below min
        assert bounds.normalize(-5.0) == 0.0

        # Above max
        assert bounds.normalize(15.0) == 1.0

    def test_normalize_zero_range(self):
        """Test normalization when min equals max."""
        bounds = NormalizationBounds(min_val=5.0, max_val=5.0)

        # Should return 0.5 for no variance
        assert bounds.normalize(5.0) == 0.5
        assert bounds.normalize(10.0) == 0.5


class TestObjectiveWeights:
    """Tests for ObjectiveWeights."""

    def test_default_weights(self):
        """Test default weight values."""
        weights = ObjectiveWeights()

        assert weights.sharpe_ratio == 0.35
        assert weights.profit_factor == 0.30
        assert weights.calmar_ratio == 0.35
        assert weights.drawdown_penalty == -0.20

    def test_custom_weights(self):
        """Test custom weight values."""
        weights = ObjectiveWeights(sharpe_ratio=0.4, profit_factor=0.3, calmar_ratio=0.3, drawdown_penalty=-0.1)

        assert weights.sharpe_ratio == 0.4
        assert weights.profit_factor == 0.3
        assert weights.calmar_ratio == 0.3
        assert weights.drawdown_penalty == -0.1

    def test_invalid_weights_sum(self):
        """Test that invalid weight sums raise error."""
        with pytest.raises(ValueError, match="must sum to ~1.0"):
            ObjectiveWeights(sharpe_ratio=0.1, profit_factor=0.1, calmar_ratio=0.1, drawdown_penalty=-0.2)


class TestMultiObjectiveFunction:
    """Tests for MultiObjectiveFunction."""

    def test_initialization(self):
        """Test multi-objective function initialization."""
        func = MultiObjectiveFunction()

        assert func.weights is not None
        assert func.statistics is not None
        assert func.adaptive_normalization is True
        assert func._trial_count == 0

    def test_calculate_score_valid_metrics(self):
        """Test score calculation with valid metrics."""
        func = MultiObjectiveFunction()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=5.0,
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
            time_in_market_pct=80.0,
            avg_ladder_depth_long=5.0,
            avg_ladder_depth_short=5.0,
            ladder_fill_rate_pct=30.0,
            avg_mae_pct=0.5,
            avg_mfe_pct=1.0,
        )

        score = func.calculate_score(metrics)

        # Score should be finite and reasonable
        assert math.isfinite(score)
        assert -1.0 <= score <= 1.0  # Normalized components

    def test_calculate_score_invalid_metrics(self):
        """Test score calculation with invalid metrics."""
        func = MultiObjectiveFunction()

        # Metrics with NaN values
        metrics = PerformanceMetrics(
            total_pnl=float("nan"),
            total_return_pct=float("nan"),
            annualized_return_pct=float("nan"),
            sharpe_ratio=float("nan"),
            sortino_ratio=None,
            calmar_ratio=None,
            max_drawdown_pct=None,
            max_drawdown_duration_days=None,
            total_trades=None,
            winning_trades=None,
            losing_trades=None,
            win_rate_pct=None,
            avg_win=None,
            avg_loss=None,
            profit_factor=None,
            avg_trade_pnl=None,
            maker_fill_ratio=None,
            avg_slippage_bps=None,
            total_fees_paid=None,
            funding_paid=None,
            funding_received=None,
            net_funding_pnl=None,
            avg_long_exposure=None,
            avg_short_exposure=None,
            max_long_exposure=None,
            max_short_exposure=None,
            time_in_market_pct=None,
            avg_ladder_depth_long=None,
            avg_ladder_depth_short=None,
            ladder_fill_rate_pct=None,
            avg_mae_pct=None,
            avg_mfe_pct=None,
        )

        score = func.calculate_score(metrics)

        # Should return -inf for invalid metrics
        assert score == float("-inf")

    def test_calculate_score_too_few_trades(self):
        """Test that strategies with too few trades are rejected."""
        func = MultiObjectiveFunction()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=5.0,
            max_drawdown_duration_days=10,
            total_trades=3,  # Too few (below 5-trade minimum)
            winning_trades=2,
            losing_trades=1,
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
            time_in_market_pct=80.0,
            avg_ladder_depth_long=5.0,
            avg_ladder_depth_short=5.0,
            ladder_fill_rate_pct=30.0,
            avg_mae_pct=0.5,
            avg_mfe_pct=1.0,
        )

        score = func.calculate_score(metrics)

        # Should return -1000 for too few trades (allows optimizer to differentiate)
        assert score == -1000.0

    def test_calculate_score_complete_loss(self):
        """Test that complete loss strategies are rejected."""
        func = MultiObjectiveFunction()

        metrics = PerformanceMetrics(
            total_pnl=-10000.0,
            total_return_pct=-100.0,
            annualized_return_pct=-100.0,
            sharpe_ratio=-5.0,
            sortino_ratio=-5.0,
            calmar_ratio=-10.0,
            max_drawdown_pct=100.0,  # Complete loss
            max_drawdown_duration_days=365,
            total_trades=100,
            winning_trades=10,
            losing_trades=90,
            win_rate_pct=10.0,
            avg_win=10.0,
            avg_loss=-100.0,
            profit_factor=0.1,
            avg_trade_pnl=-100.0,
            maker_fill_ratio=0.95,
            avg_slippage_bps=0.5,
            total_fees_paid=500.0,
            funding_paid=100.0,
            funding_received=0.0,
            net_funding_pnl=-100.0,
            avg_long_exposure=0.5,
            avg_short_exposure=0.5,
            max_long_exposure=1.0,
            max_short_exposure=1.0,
            time_in_market_pct=80.0,
            avg_ladder_depth_long=5.0,
            avg_ladder_depth_short=5.0,
            ladder_fill_rate_pct=30.0,
            avg_mae_pct=0.5,
            avg_mfe_pct=1.0,
        )

        score = func.calculate_score(metrics)

        # Should return -inf for complete loss
        assert score == float("-inf")

    def test_get_component_scores(self):
        """Test retrieval of component scores."""
        func = MultiObjectiveFunction()

        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=5.0,
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
            time_in_market_pct=80.0,
            avg_ladder_depth_long=5.0,
            avg_ladder_depth_short=5.0,
            ladder_fill_rate_pct=30.0,
            avg_mae_pct=0.5,
            avg_mfe_pct=1.0,
        )

        components = func.get_component_scores(metrics)

        # Check all components are present
        assert "sharpe_raw" in components
        assert "sharpe_norm" in components
        assert "sharpe_weighted" in components
        assert "profit_raw" in components
        assert "profit_norm" in components
        assert "profit_weighted" in components
        assert "calmar_raw" in components
        assert "calmar_norm" in components
        assert "calmar_weighted" in components
        assert "drawdown_raw" in components
        assert "drawdown_norm" in components
        assert "drawdown_weighted" in components
        assert "total_score" in components

    def test_reset_statistics(self):
        """Test resetting tracked statistics."""
        func = MultiObjectiveFunction()

        # Add some observations
        metrics = PerformanceMetrics(
            total_pnl=1000.0,
            total_return_pct=10.0,
            annualized_return_pct=12.0,
            sharpe_ratio=2.5,
            sortino_ratio=3.0,
            calmar_ratio=2.0,
            max_drawdown_pct=5.0,
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
            time_in_market_pct=80.0,
            avg_ladder_depth_long=5.0,
            avg_ladder_depth_short=5.0,
            ladder_fill_rate_pct=30.0,
            avg_mae_pct=0.5,
            avg_mfe_pct=1.0,
        )

        func.calculate_score(metrics)
        assert len(func.statistics.observed_sharpe) > 0

        # Reset
        func.reset_statistics()

        assert len(func.statistics.observed_sharpe) == 0
        assert func._trial_count == 0
