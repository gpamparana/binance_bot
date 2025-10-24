"""Hard constraints validator for strategy optimization.

This module defines the minimum performance requirements that
a strategy parameter set must meet to be considered valid.
"""

from dataclasses import dataclass

from naut_hedgegrid.metrics.report import PerformanceMetrics


@dataclass
class ConstraintThresholds:
    """
    Configurable thresholds for optimization constraints.

    These thresholds define the minimum acceptable performance
    metrics that a strategy must achieve. Parameters that don't
    meet these constraints are rejected during optimization.

    Attributes
    ----------
    min_sharpe_ratio : float
        Minimum acceptable Sharpe ratio (default 1.0)
    max_drawdown_pct : float
        Maximum acceptable drawdown percentage (default 20%)
    min_trades : int
        Minimum number of trades required (default 50)
    min_win_rate_pct : float
        Minimum win rate percentage (default 45%)
    min_profit_factor : float
        Minimum profit factor (default 1.1)
    min_calmar_ratio : float
        Minimum Calmar ratio (default 0.5)
    """

    min_sharpe_ratio: float = 1.0
    max_drawdown_pct: float = 20.0
    min_trades: int = 50
    min_win_rate_pct: float = 45.0
    min_profit_factor: float = 1.1
    min_calmar_ratio: float = 0.5

    def __post_init__(self):
        """Validate constraint thresholds."""
        if self.min_sharpe_ratio < -5:
            raise ValueError("Minimum Sharpe ratio too low")
        if self.max_drawdown_pct <= 0 or self.max_drawdown_pct > 100:
            raise ValueError("Max drawdown must be between 0 and 100%")
        if self.min_trades < 0:
            raise ValueError("Minimum trades must be non-negative")
        if self.min_win_rate_pct < 0 or self.min_win_rate_pct > 100:
            raise ValueError("Min win rate must be between 0 and 100%")
        if self.min_profit_factor < 0:
            raise ValueError("Min profit factor must be non-negative")


class ConstraintsValidator:
    """
    Validates that strategy performance meets hard constraints.

    This validator enforces minimum performance requirements to ensure
    that only viable parameter combinations are considered during
    optimization. It helps filter out strategies that:
    - Have poor risk-adjusted returns
    - Experience excessive drawdowns
    - Generate too few trades for statistical significance
    - Have unacceptable win rates or profit factors

    The validator can be configured with custom thresholds and can
    operate in strict or lenient modes.

    Attributes
    ----------
    thresholds : ConstraintThresholds
        Performance thresholds for validation
    strict_mode : bool
        If True, all constraints must be met. If False, uses
        a scoring system where most constraints should be met.
    """

    def __init__(
        self,
        thresholds: ConstraintThresholds | None = None,
        strict_mode: bool = True
    ):
        """
        Initialize constraints validator.

        Parameters
        ----------
        thresholds : ConstraintThresholds, optional
            Custom constraint thresholds (uses defaults if None)
        strict_mode : bool
            If True, all constraints must be met for validation to pass
        """
        self.thresholds = thresholds or ConstraintThresholds()
        self.strict_mode = strict_mode

    def is_valid(self, metrics: PerformanceMetrics) -> bool:
        """
        Check if metrics meet all constraints.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Strategy performance metrics to validate

        Returns
        -------
        bool
            True if all constraints are satisfied, False otherwise
        """
        try:
            violations = self.get_violations(metrics)

            if self.strict_mode:
                # In strict mode, any violation fails validation
                return len(violations) == 0
            # In lenient mode, allow up to 1 minor violation
            return len(violations) <= 1

        except (AttributeError, TypeError, ValueError):
            # Invalid metrics fail validation
            return False

    def get_violations(self, metrics: PerformanceMetrics) -> list[str]:
        """
        Get list of constraint violations.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Strategy performance metrics to validate

        Returns
        -------
        list[str]
            List of violated constraints with descriptions
        """
        violations = []

        try:
            # Check Sharpe ratio
            if metrics.sharpe_ratio is None or metrics.sharpe_ratio < self.thresholds.min_sharpe_ratio:
                sharpe = metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 0
                violations.append(
                    f"Sharpe ratio {sharpe:.2f} < {self.thresholds.min_sharpe_ratio:.2f}"
                )

            # Check maximum drawdown
            if metrics.max_drawdown_pct is None or metrics.max_drawdown_pct > self.thresholds.max_drawdown_pct:
                dd = metrics.max_drawdown_pct if metrics.max_drawdown_pct is not None else 100
                violations.append(
                    f"Max drawdown {dd:.1f}% > {self.thresholds.max_drawdown_pct:.1f}%"
                )

            # Check trade count
            if metrics.total_trades is None or metrics.total_trades < self.thresholds.min_trades:
                trades = metrics.total_trades if metrics.total_trades is not None else 0
                violations.append(
                    f"Trade count {trades} < {self.thresholds.min_trades}"
                )

            # Check win rate
            if metrics.win_rate_pct is None or metrics.win_rate_pct < self.thresholds.min_win_rate_pct:
                win_rate = metrics.win_rate_pct if metrics.win_rate_pct is not None else 0
                violations.append(
                    f"Win rate {win_rate:.1f}% < {self.thresholds.min_win_rate_pct:.1f}%"
                )

            # Check profit factor
            if metrics.profit_factor is None or metrics.profit_factor < self.thresholds.min_profit_factor:
                pf = metrics.profit_factor if metrics.profit_factor is not None else 0
                violations.append(
                    f"Profit factor {pf:.2f} < {self.thresholds.min_profit_factor:.2f}"
                )

            # Check Calmar ratio
            if metrics.calmar_ratio is None or metrics.calmar_ratio < self.thresholds.min_calmar_ratio:
                calmar = metrics.calmar_ratio if metrics.calmar_ratio is not None else 0
                violations.append(
                    f"Calmar ratio {calmar:.2f} < {self.thresholds.min_calmar_ratio:.2f}"
                )

        except (AttributeError, TypeError) as e:
            violations.append(f"Invalid metrics: {e!s}")

        return violations

    def get_violation_score(self, metrics: PerformanceMetrics) -> float:
        """
        Calculate a violation score (0 = no violations, higher = more/worse violations).

        This method provides a continuous measure of constraint satisfaction,
        useful for optimization algorithms that need gradient information.

        Parameters
        ----------
        metrics : PerformanceMetrics
            Strategy performance metrics to score

        Returns
        -------
        float
            Violation score (0.0 = perfect, higher = worse)
        """
        score = 0.0

        try:
            # Sharpe ratio violation
            if metrics.sharpe_ratio is None or metrics.sharpe_ratio < self.thresholds.min_sharpe_ratio:
                sharpe = metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 0
                score += max(0, self.thresholds.min_sharpe_ratio - sharpe)

            # Drawdown violation (scaled by 0.1 to match other metrics)
            if metrics.max_drawdown_pct is None or metrics.max_drawdown_pct > self.thresholds.max_drawdown_pct:
                dd = metrics.max_drawdown_pct if metrics.max_drawdown_pct is not None else 100
                score += max(0, dd - self.thresholds.max_drawdown_pct) * 0.1

            # Trade count violation (scaled)
            if metrics.total_trades is None or metrics.total_trades < self.thresholds.min_trades:
                trades = metrics.total_trades if metrics.total_trades is not None else 0
                score += max(0, self.thresholds.min_trades - trades) * 0.01

            # Win rate violation (scaled)
            if metrics.win_rate_pct is None or metrics.win_rate_pct < self.thresholds.min_win_rate_pct:
                win_rate = metrics.win_rate_pct if metrics.win_rate_pct is not None else 0
                score += max(0, self.thresholds.min_win_rate_pct - win_rate) * 0.01

            # Profit factor violation
            if metrics.profit_factor is None or metrics.profit_factor < self.thresholds.min_profit_factor:
                pf = metrics.profit_factor if metrics.profit_factor is not None else 0
                score += max(0, self.thresholds.min_profit_factor - pf)

            # Calmar ratio violation
            if metrics.calmar_ratio is None or metrics.calmar_ratio < self.thresholds.min_calmar_ratio:
                calmar = metrics.calmar_ratio if metrics.calmar_ratio is not None else 0
                score += max(0, self.thresholds.min_calmar_ratio - calmar)

        except (AttributeError, TypeError):
            # Return high penalty for invalid metrics
            score = 1000.0

        return score

    def update_thresholds(self, **kwargs):
        """
        Update constraint thresholds dynamically.

        Parameters
        ----------
        **kwargs
            Keyword arguments matching ConstraintThresholds attributes
        """
        for key, value in kwargs.items():
            if hasattr(self.thresholds, key):
                setattr(self.thresholds, key, value)
            else:
                raise ValueError(f"Unknown threshold parameter: {key}")
