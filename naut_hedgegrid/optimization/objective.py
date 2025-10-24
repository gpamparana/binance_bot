"""Multi-objective function for strategy optimization.

This module implements a weighted multi-objective function that combines
multiple performance metrics into a single optimization score.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from naut_hedgegrid.metrics.report import PerformanceMetrics


@dataclass
class ObjectiveWeights:
    """Weights for combining multiple objectives."""

    sharpe_ratio: float = 0.35
    profit_factor: float = 0.30
    calmar_ratio: float = 0.35
    drawdown_penalty: float = -0.20

    def __post_init__(self):
        """Validate weights sum to approximately 1.0 (ignoring penalty)."""
        positive_sum = self.sharpe_ratio + self.profit_factor + self.calmar_ratio
        if not 0.95 <= positive_sum <= 1.05:
            raise ValueError(f"Positive weights must sum to ~1.0, got {positive_sum:.2f}")


@dataclass
class NormalizationBounds:
    """Min-max bounds for metric normalization."""

    min_val: float
    max_val: float

    def normalize(self, value: float) -> float:
        """Normalize value to [0, 1] range using min-max scaling."""
        if self.max_val == self.min_val:
            return 0.5  # No variance, return middle value

        # Clip to bounds to handle outliers
        clipped = np.clip(value, self.min_val, self.max_val)
        return (clipped - self.min_val) / (self.max_val - self.min_val)


@dataclass
class MetricStatistics:
    """Tracks statistics for normalizing metrics across trials."""

    sharpe_bounds: NormalizationBounds = field(default_factory=lambda: NormalizationBounds(-1.0, 5.0))
    profit_bounds: NormalizationBounds = field(default_factory=lambda: NormalizationBounds(0.5, 3.0))
    calmar_bounds: NormalizationBounds = field(default_factory=lambda: NormalizationBounds(-1.0, 5.0))
    drawdown_bounds: NormalizationBounds = field(default_factory=lambda: NormalizationBounds(0.0, 50.0))

    # Track observed values for adaptive normalization
    observed_sharpe: list[float] = field(default_factory=list)
    observed_profit: list[float] = field(default_factory=list)
    observed_calmar: list[float] = field(default_factory=list)
    observed_drawdown: list[float] = field(default_factory=list)

    def update_bounds_from_observations(self, percentile_low: float = 5, percentile_high: float = 95):
        """
        Update normalization bounds based on observed values.

        Uses percentiles to be robust to outliers.

        Parameters
        ----------
        percentile_low : float
            Lower percentile for min bound (default 5th percentile)
        percentile_high : float
            Upper percentile for max bound (default 95th percentile)
        """
        if len(self.observed_sharpe) >= 10:
            self.sharpe_bounds = NormalizationBounds(
                np.percentile(self.observed_sharpe, percentile_low),
                np.percentile(self.observed_sharpe, percentile_high)
            )

        if len(self.observed_profit) >= 10:
            self.profit_bounds = NormalizationBounds(
                np.percentile(self.observed_profit, percentile_low),
                np.percentile(self.observed_profit, percentile_high)
            )

        if len(self.observed_calmar) >= 10:
            self.calmar_bounds = NormalizationBounds(
                np.percentile(self.observed_calmar, percentile_low),
                np.percentile(self.observed_calmar, percentile_high)
            )

        if len(self.observed_drawdown) >= 10:
            self.drawdown_bounds = NormalizationBounds(
                np.percentile(self.observed_drawdown, percentile_low),
                np.percentile(self.observed_drawdown, percentile_high)
            )


class MultiObjectiveFunction:
    """
    Multi-objective function for strategy optimization.

    This class combines multiple performance metrics into a single
    optimization score using weighted normalization. The score is
    designed to balance risk-adjusted returns (Sharpe), profitability
    (profit factor), drawdown resilience (Calmar), and downside
    protection (drawdown penalty).

    The function uses min-max normalization to ensure fair weighting
    across metrics with different scales, and tracks statistics across
    trials for adaptive normalization.

    Attributes
    ----------
    weights : ObjectiveWeights
        Weights for combining objectives
    statistics : MetricStatistics
        Tracks metric statistics for normalization
    adaptive_normalization : bool
        Whether to update bounds based on observations
    """

    def __init__(
        self,
        weights: ObjectiveWeights | None = None,
        adaptive_normalization: bool = True
    ):
        """
        Initialize multi-objective function.

        Parameters
        ----------
        weights : ObjectiveWeights, optional
            Custom weights for objectives (uses defaults if None)
        adaptive_normalization : bool
            Whether to adaptively update normalization bounds
        """
        self.weights = weights or ObjectiveWeights()
        self.statistics = MetricStatistics()
        self.adaptive_normalization = adaptive_normalization
        self._trial_count = 0

    def calculate_score(self, metrics: PerformanceMetrics) -> float:
        """
        Calculate optimization score from performance metrics.

        The score combines multiple objectives:
        1. Sharpe ratio: Risk-adjusted returns
        2. Profit factor: Win/loss ratio
        3. Calmar ratio: Return relative to max drawdown
        4. Drawdown penalty: Penalizes large drawdowns

        Parameters
        ----------
        metrics : PerformanceMetrics
            Backtest performance metrics

        Returns
        -------
        float
            Combined optimization score (higher is better)
            Returns -inf if metrics are invalid or missing
        """
        try:
            # Extract key metrics
            sharpe = metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 0.0
            profit_factor = metrics.profit_factor if metrics.profit_factor is not None else 1.0
            calmar = metrics.calmar_ratio if metrics.calmar_ratio is not None else 0.0
            max_dd = metrics.max_drawdown_pct if metrics.max_drawdown_pct is not None else 100.0

            # Handle invalid metrics
            if math.isnan(sharpe) or math.isnan(profit_factor) or math.isnan(calmar) or math.isnan(max_dd):
                return float("-inf")

            # Check for extreme/invalid values
            if max_dd >= 100.0:  # Complete loss
                return float("-inf")

            if metrics.total_trades is not None and metrics.total_trades < 10:
                # Too few trades to be meaningful
                return float("-inf")

            # Track observations for adaptive normalization
            self.statistics.observed_sharpe.append(sharpe)
            self.statistics.observed_profit.append(profit_factor)
            self.statistics.observed_calmar.append(calmar)
            self.statistics.observed_drawdown.append(max_dd)

            # Update normalization bounds adaptively
            self._trial_count += 1
            if self.adaptive_normalization and self._trial_count % 20 == 0:
                self.statistics.update_bounds_from_observations()

            # Normalize metrics to [0, 1] range
            norm_sharpe = self.statistics.sharpe_bounds.normalize(sharpe)
            norm_profit = self.statistics.profit_bounds.normalize(profit_factor)
            norm_calmar = self.statistics.calmar_bounds.normalize(calmar)
            norm_drawdown = self.statistics.drawdown_bounds.normalize(max_dd)

            # Calculate weighted score
            score = (
                self.weights.sharpe_ratio * norm_sharpe +
                self.weights.profit_factor * norm_profit +
                self.weights.calmar_ratio * norm_calmar +
                self.weights.drawdown_penalty * norm_drawdown  # Penalty (negative weight)
            )

            return score

        except (AttributeError, TypeError, ValueError):
            # Return worst possible score on error
            return float("-inf")

    def get_component_scores(self, metrics: PerformanceMetrics) -> dict[str, float]:
        """
        Get individual component scores for debugging.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Backtest performance metrics

        Returns
        -------
        Dict[str, float]
            Individual normalized scores for each component
        """
        try:
            sharpe = metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 0.0
            profit_factor = metrics.profit_factor if metrics.profit_factor is not None else 1.0
            calmar = metrics.calmar_ratio if metrics.calmar_ratio is not None else 0.0
            max_dd = metrics.max_drawdown_pct if metrics.max_drawdown_pct is not None else 100.0

            # Normalize metrics
            norm_sharpe = self.statistics.sharpe_bounds.normalize(sharpe)
            norm_profit = self.statistics.profit_bounds.normalize(profit_factor)
            norm_calmar = self.statistics.calmar_bounds.normalize(calmar)
            norm_drawdown = self.statistics.drawdown_bounds.normalize(max_dd)

            return {
                "sharpe_raw": sharpe,
                "sharpe_norm": norm_sharpe,
                "sharpe_weighted": self.weights.sharpe_ratio * norm_sharpe,
                "profit_raw": profit_factor,
                "profit_norm": norm_profit,
                "profit_weighted": self.weights.profit_factor * norm_profit,
                "calmar_raw": calmar,
                "calmar_norm": norm_calmar,
                "calmar_weighted": self.weights.calmar_ratio * norm_calmar,
                "drawdown_raw": max_dd,
                "drawdown_norm": norm_drawdown,
                "drawdown_weighted": self.weights.drawdown_penalty * norm_drawdown,
                "total_score": self.calculate_score(metrics),
            }

        except (AttributeError, TypeError, ValueError):
            return {
                "error": "Failed to calculate component scores",
                "total_score": float("-inf"),
            }

    def reset_statistics(self):
        """Reset tracked statistics for new optimization run."""
        self.statistics = MetricStatistics()
        self._trial_count = 0
