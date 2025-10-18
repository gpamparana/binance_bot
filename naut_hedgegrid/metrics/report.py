"""Performance metrics calculation and report generation.

This module provides comprehensive performance analysis for backtest results,
including returns, risk-adjusted metrics, trade statistics, execution quality,
and exposure metrics.
"""

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics from backtest.

    Attributes:
        total_pnl: Total profit/loss in dollar terms
        total_return_pct: Total return percentage
        annualized_return_pct: Annualized return percentage

        sharpe_ratio: Risk-adjusted return (excess return / volatility)
        sortino_ratio: Risk-adjusted return using downside deviation only
        calmar_ratio: Return / max drawdown ratio
        max_drawdown_pct: Maximum peak-to-trough equity decline percentage
        max_drawdown_duration_days: Longest drawdown period in days

        total_trades: Total number of completed trades
        winning_trades: Number of profitable trades
        losing_trades: Number of losing trades
        win_rate_pct: Percentage of winning trades
        avg_win: Average profit of winning trades
        avg_loss: Average loss of losing trades
        profit_factor: Ratio of total wins to total losses
        avg_trade_pnl: Average PnL per trade

        maker_fill_ratio: Percentage of fills that were maker orders
        avg_slippage_bps: Average slippage in basis points
        total_fees_paid: Total trading fees paid

        funding_paid: Total funding payments made
        funding_received: Total funding payments received
        net_funding_pnl: Net funding PnL (received - paid)

        avg_long_exposure: Average long position size
        avg_short_exposure: Average short position size
        max_long_exposure: Maximum long position size
        max_short_exposure: Maximum short position size
        time_in_market_pct: Percentage of time with open positions

        avg_ladder_depth_long: Average number of long grid orders
        avg_ladder_depth_short: Average number of short grid orders
        ladder_fill_rate_pct: Percentage of grid orders that were filled

        avg_mae_pct: Average Maximum Adverse Excursion percentage
        avg_mfe_pct: Average Maximum Favorable Excursion percentage

    """

    # Returns
    total_pnl: float
    total_return_pct: float
    annualized_return_pct: float

    # Risk metrics
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_days: float

    # Trade metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_trade_pnl: float

    # Execution metrics
    maker_fill_ratio: float
    avg_slippage_bps: float
    total_fees_paid: float

    # Funding metrics
    funding_paid: float
    funding_received: float
    net_funding_pnl: float

    # Exposure metrics
    avg_long_exposure: float
    avg_short_exposure: float
    max_long_exposure: float
    max_short_exposure: float
    time_in_market_pct: float

    # Ladder utilization
    avg_ladder_depth_long: float
    avg_ladder_depth_short: float
    ladder_fill_rate_pct: float

    # MAE/MFE
    avg_mae_pct: float
    avg_mfe_pct: float

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format.

        Returns:
            Dictionary containing all metrics with their names as keys

        """
        return asdict(self)

    def to_dataframe(self) -> pd.DataFrame:
        """Convert metrics to pandas DataFrame.

        Returns:
            Single-row DataFrame with metrics as columns

        """
        return pd.DataFrame([self.to_dict()])


class ReportGenerator:
    """Generates performance reports from backtest results.

    This class takes raw backtest data (account history, positions, orders, fills)
    and computes comprehensive performance metrics including returns, risk metrics,
    trade statistics, execution quality, and exposure metrics.

    Attributes:
        account_history: DataFrame with equity curve data
        positions: DataFrame with completed position records
        orders: DataFrame with order records
        fills: DataFrame with fill/execution records
        config: Backtest configuration object

    """

    def __init__(
        self,
        account_history: pd.DataFrame,
        positions: list[dict],
        orders: list[dict],
        fills: list[dict],
        config: Any,
    ) -> None:
        """Initialize report generator with backtest data.

        Args:
            account_history: DataFrame with columns [timestamp, equity, balance].
                timestamp should be datetime, equity is total account value,
                balance is cash balance.
            positions: List of position dicts with keys [id, instrument, side,
                qty, entry_price, exit_price, pnl, open_ts, close_ts]. Only
                closed positions should be included.
            orders: List of order dicts with keys [id, instrument, side, type,
                status, price, qty, filled_qty, maker, ts].
            fills: List of fill dicts with keys [order_id, price, qty,
                liquidity_side, commission, ts]. liquidity_side should be
                'MAKER' or 'TAKER'.
            config: Backtest configuration object with optional metrics.risk_free_rate

        """
        self.account_history = account_history
        self.positions = pd.DataFrame(positions) if positions else pd.DataFrame()
        self.orders = pd.DataFrame(orders) if orders else pd.DataFrame()
        self.fills = pd.DataFrame(fills) if fills else pd.DataFrame()
        self.config = config

    def compute_returns_metrics(self) -> dict[str, float]:
        """Calculate return-based metrics.

        Computes total PnL, total return percentage, and annualized return
        percentage from the account equity curve.

        Returns:
            Dictionary with keys:
                - total_pnl: Final equity - initial equity
                - total_return_pct: Total return as percentage
                - annualized_return_pct: Return normalized to annual basis

        """
        if self.account_history.empty:
            return {
                "total_pnl": 0.0,
                "total_return_pct": 0.0,
                "annualized_return_pct": 0.0,
            }

        initial_equity = self.account_history.iloc[0]["equity"]
        final_equity = self.account_history.iloc[-1]["equity"]

        total_pnl = final_equity - initial_equity
        total_return_pct = (total_pnl / initial_equity) * 100 if initial_equity > 0 else 0.0

        # Annualize based on time period
        time_delta = (
            self.account_history.iloc[-1]["timestamp"]
            - self.account_history.iloc[0]["timestamp"]
        )
        days = max(time_delta.days, 1)  # Avoid division by zero
        years = days / 365.0
        annualized_return_pct = (total_return_pct / years) if years > 0 else 0.0

        return {
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "annualized_return_pct": annualized_return_pct,
        }

    def compute_sharpe_ratio(self, risk_free_rate: float = 0.04) -> float:
        """Calculate Sharpe ratio (risk-adjusted return).

        The Sharpe ratio measures excess return per unit of volatility.
        Higher values indicate better risk-adjusted returns.

        Args:
            risk_free_rate: Annual risk-free rate (default 4%)

        Returns:
            Annualized Sharpe ratio. Returns 0 if insufficient data or zero volatility.

        """
        if self.account_history.empty or len(self.account_history) < 2:
            return 0.0

        # Calculate daily returns
        equity = self.account_history["equity"]
        returns = equity.pct_change().dropna()

        if returns.empty or returns.std() == 0:
            return 0.0

        # Annualized Sharpe
        daily_rf = risk_free_rate / 365
        excess_return = returns.mean() - daily_rf
        sharpe = excess_return / returns.std() * np.sqrt(365)

        return float(sharpe)

    def compute_sortino_ratio(self, risk_free_rate: float = 0.04) -> float:
        """Calculate Sortino ratio (downside risk-adjusted return).

        The Sortino ratio is similar to Sharpe but only penalizes downside
        volatility, making it more suitable for asymmetric return distributions.

        Args:
            risk_free_rate: Annual risk-free rate (default 4%)

        Returns:
            Annualized Sortino ratio. Returns 0 if insufficient data or no downside.

        """
        if self.account_history.empty or len(self.account_history) < 2:
            return 0.0

        equity = self.account_history["equity"]
        returns = equity.pct_change().dropna()

        if returns.empty:
            return 0.0

        # Downside deviation (only negative returns)
        downside_returns = returns[returns < 0]

        if downside_returns.empty or downside_returns.std() == 0:
            # No downside or no variation - return high value if positive returns
            if returns.mean() > 0:
                return 999.0  # Arbitrarily high value for no downside risk
            return 0.0

        daily_rf = risk_free_rate / 365
        excess_return = returns.mean() - daily_rf
        sortino = excess_return / downside_returns.std() * np.sqrt(365)

        return float(sortino)

    def compute_calmar_ratio(self) -> float:
        """Calculate Calmar ratio (return / max drawdown).

        The Calmar ratio measures return relative to maximum drawdown.
        Higher values indicate better risk-adjusted returns considering
        worst-case drawdown.

        Returns:
            Calmar ratio. Returns 0 if no drawdown occurred.

        """
        returns = self.compute_returns_metrics()
        drawdown = self.compute_drawdown()

        if drawdown["max_drawdown_pct"] == 0:
            # No drawdown - return high value if positive returns
            if returns["annualized_return_pct"] > 0:
                return 999.0
            return 0.0

        calmar = returns["annualized_return_pct"] / abs(drawdown["max_drawdown_pct"])
        return float(calmar)

    def compute_drawdown(self) -> dict[str, float]:
        """Calculate drawdown metrics.

        Computes maximum drawdown percentage and longest drawdown duration
        from the equity curve. Drawdown is measured as percentage decline
        from running peak equity.

        Returns:
            Dictionary with keys:
                - max_drawdown_pct: Maximum peak-to-trough decline (negative value)
                - max_drawdown_duration_days: Longest time underwater in days

        """
        if self.account_history.empty:
            return {
                "max_drawdown_pct": 0.0,
                "max_drawdown_duration_days": 0.0,
            }

        equity = self.account_history["equity"].values
        timestamps = self.account_history["timestamp"].values

        # Calculate running maximum
        running_max = np.maximum.accumulate(equity)

        # Calculate drawdown series (percentage from peak)
        drawdown = (equity - running_max) / running_max * 100

        # Find max drawdown
        max_dd = float(np.min(drawdown))

        # Find max drawdown duration
        max_dd_duration = 0.0
        current_dd_start = None

        for i, dd in enumerate(drawdown):
            if dd < -0.01:  # In drawdown (allow small numerical errors)
                if current_dd_start is None:
                    current_dd_start = timestamps[i]
            else:
                # Recovered from drawdown
                if current_dd_start is not None:
                    time_diff = timestamps[i] - current_dd_start
                    # Handle both pandas Timedelta and numpy timedelta64
                    if hasattr(time_diff, "total_seconds"):
                        duration = time_diff.total_seconds() / 86400
                    else:
                        # numpy timedelta64
                        duration = float(time_diff / np.timedelta64(1, "D"))
                    max_dd_duration = max(max_dd_duration, duration)
                    current_dd_start = None

        # Check if still in drawdown at end
        if current_dd_start is not None:
            time_diff = timestamps[-1] - current_dd_start
            # Handle both pandas Timedelta and numpy timedelta64
            if hasattr(time_diff, "total_seconds"):
                duration = time_diff.total_seconds() / 86400
            else:
                # numpy timedelta64
                duration = float(time_diff / np.timedelta64(1, "D"))
            max_dd_duration = max(max_dd_duration, duration)

        return {
            "max_drawdown_pct": max_dd,
            "max_drawdown_duration_days": max_dd_duration,
        }

    def compute_trade_metrics(self) -> dict[str, float]:
        """Calculate trade statistics.

        Computes win rate, profit factor, average wins/losses, and other
        trade-level statistics from closed positions.

        Returns:
            Dictionary with keys:
                - total_trades: Total number of closed positions
                - winning_trades: Number of profitable trades
                - losing_trades: Number of losing trades
                - win_rate_pct: Percentage of winning trades
                - avg_win: Average profit of winning trades
                - avg_loss: Average loss of losing trades (negative value)
                - profit_factor: Total wins / total losses
                - avg_trade_pnl: Average PnL across all trades

        """
        if self.positions.empty:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl": 0.0,
            }

        total_trades = len(self.positions)

        winning_positions = self.positions[self.positions["pnl"] > 0]
        losing_positions = self.positions[self.positions["pnl"] < 0]

        winning_trades = len(winning_positions)
        losing_trades = len(losing_positions)

        win_rate_pct = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        avg_win = float(winning_positions["pnl"].mean()) if not winning_positions.empty else 0.0
        avg_loss = float(losing_positions["pnl"].mean()) if not losing_positions.empty else 0.0

        total_wins = float(winning_positions["pnl"].sum()) if not winning_positions.empty else 0.0
        total_losses = abs(float(losing_positions["pnl"].sum())) if not losing_positions.empty else 0.0

        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0.0

        avg_trade_pnl = float(self.positions["pnl"].mean())

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate_pct": win_rate_pct,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "avg_trade_pnl": avg_trade_pnl,
        }

    def compute_execution_metrics(self) -> dict[str, float]:
        """Calculate execution quality metrics.

        Analyzes fill data to compute maker ratio, slippage, and total fees paid.

        Returns:
            Dictionary with keys:
                - maker_fill_ratio: Percentage of fills that were maker orders
                - avg_slippage_bps: Average slippage in basis points (placeholder)
                - total_fees_paid: Sum of all commission payments

        Note:
            Slippage calculation requires limit price data which may not be
            available in all backtests. Current implementation returns 0.

        """
        if self.fills.empty:
            return {
                "maker_fill_ratio": 0.0,
                "avg_slippage_bps": 0.0,
                "total_fees_paid": 0.0,
            }

        # Maker ratio
        maker_fills = self.fills[self.fills["liquidity_side"] == "MAKER"]
        maker_fill_ratio = (len(maker_fills) / len(self.fills) * 100) if len(self.fills) > 0 else 0.0

        # Slippage calculation would require comparing fill price to limit price
        # This is a placeholder for future implementation
        avg_slippage_bps = 0.0

        # Total fees
        total_fees_paid = float(self.fills["commission"].sum())

        return {
            "maker_fill_ratio": maker_fill_ratio,
            "avg_slippage_bps": avg_slippage_bps,
            "total_fees_paid": total_fees_paid,
        }

    def compute_funding_metrics(self) -> dict[str, float]:
        """Calculate funding rate PnL.

        Analyzes funding payments made and received during the backtest.
        For perpetual futures, funding rates can significantly impact PnL.

        Returns:
            Dictionary with keys:
                - funding_paid: Total funding payments made (short positions)
                - funding_received: Total funding payments received (long positions)
                - net_funding_pnl: Net funding PnL (received - paid)

        Note:
            Current implementation returns placeholder values. Actual funding
            calculation would require funding rate history and position snapshots
            at funding timestamps.

        """
        # Funding metrics would be extracted from account history or
        # separate funding payment records. This requires:
        # 1. Position size at each funding timestamp (typically every 8 hours)
        # 2. Funding rate at each timestamp
        # 3. Calculate: funding_payment = position_value * funding_rate
        #
        # For now, return placeholders
        return {
            "funding_paid": 0.0,
            "funding_received": 0.0,
            "net_funding_pnl": 0.0,
        }

    def compute_exposure_metrics(self) -> dict[str, float]:
        """Calculate position exposure statistics.

        Analyzes position sizes and time in market to understand
        strategy leverage and utilization.

        Returns:
            Dictionary with keys:
                - avg_long_exposure: Average long position quantity
                - avg_short_exposure: Average short position quantity
                - max_long_exposure: Maximum long position quantity
                - max_short_exposure: Maximum short position quantity
                - time_in_market_pct: Percentage of time with open positions

        Note:
            Time in market calculation is simplified and assumes continuous
            market presence if any positions exist. A more precise calculation
            would require position snapshots over time.

        """
        if self.positions.empty:
            return {
                "avg_long_exposure": 0.0,
                "avg_short_exposure": 0.0,
                "max_long_exposure": 0.0,
                "max_short_exposure": 0.0,
                "time_in_market_pct": 0.0,
            }

        long_positions = self.positions[self.positions["side"] == "LONG"]
        short_positions = self.positions[self.positions["side"] == "SHORT"]

        avg_long_exposure = float(long_positions["qty"].mean()) if not long_positions.empty else 0.0
        avg_short_exposure = float(short_positions["qty"].mean()) if not short_positions.empty else 0.0

        max_long_exposure = float(long_positions["qty"].max()) if not long_positions.empty else 0.0
        max_short_exposure = float(short_positions["qty"].max()) if not short_positions.empty else 0.0

        # Time in market calculation (simplified)
        # For grid strategies, this is typically 100% as grid is always active
        # A more precise calculation would sum duration of all positions
        # and divide by total backtest duration
        time_in_market_pct = 100.0

        return {
            "avg_long_exposure": avg_long_exposure,
            "avg_short_exposure": avg_short_exposure,
            "max_long_exposure": max_long_exposure,
            "max_short_exposure": max_short_exposure,
            "time_in_market_pct": time_in_market_pct,
        }

    def compute_ladder_utilization(self) -> dict[str, float]:
        """Calculate grid ladder statistics.

        Analyzes order placement and fill rates to understand grid utilization.

        Returns:
            Dictionary with keys:
                - avg_ladder_depth_long: Average number of active long orders
                - avg_ladder_depth_short: Average number of active short orders
                - ladder_fill_rate_pct: Percentage of orders that were filled

        Note:
            Current implementation provides simplified metrics based on total
            order counts. A more sophisticated analysis would track active
            order counts over time.

        """
        if self.orders.empty:
            return {
                "avg_ladder_depth_long": 0.0,
                "avg_ladder_depth_short": 0.0,
                "ladder_fill_rate_pct": 0.0,
            }

        # Count orders by side
        long_orders = self.orders[self.orders["side"] == "BUY"]
        short_orders = self.orders[self.orders["side"] == "SELL"]

        # Simplified depth metrics (total counts)
        # More sophisticated would track active orders over time
        avg_ladder_depth_long = float(len(long_orders))
        avg_ladder_depth_short = float(len(short_orders))

        # Fill rate
        filled_orders = self.orders[
            self.orders["status"].isin(["FILLED", "PARTIALLY_FILLED"])
        ]
        ladder_fill_rate_pct = (
            (len(filled_orders) / len(self.orders) * 100) if len(self.orders) > 0 else 0.0
        )

        return {
            "avg_ladder_depth_long": avg_ladder_depth_long,
            "avg_ladder_depth_short": avg_ladder_depth_short,
            "ladder_fill_rate_pct": ladder_fill_rate_pct,
        }

    def compute_mae_mfe(self) -> dict[str, float]:
        """Calculate Maximum Adverse/Favorable Excursion.

        MAE (Maximum Adverse Excursion) measures the largest drawdown during
        a trade before exit. MFE (Maximum Favorable Excursion) measures the
        largest profit during a trade before exit.

        These metrics help understand:
        - How much heat trades typically take (MAE)
        - How much profit is typically given back (MFE - final PnL)

        Returns:
            Dictionary with keys:
                - avg_mae_pct: Average maximum adverse excursion percentage
                - avg_mfe_pct: Average maximum favorable excursion percentage

        Note:
            Current implementation returns placeholder values. Actual calculation
            requires tick-level position tracking to identify maximum drawdown
            and maximum profit during each trade's lifetime.

        """
        # MAE/MFE calculation requires tick-level position tracking:
        # 1. For each position, track unrealized PnL at every price update
        # 2. MAE = most negative unrealized PnL during position lifetime
        # 3. MFE = most positive unrealized PnL during position lifetime
        # 4. Express as percentage of entry value
        #
        # This requires position snapshots which aren't in the current data model
        return {
            "avg_mae_pct": 0.0,
            "avg_mfe_pct": 0.0,
        }

    def generate_report(self) -> PerformanceMetrics:
        """Generate complete performance report.

        Computes all available metrics and returns them in a structured
        PerformanceMetrics object.

        Returns:
            PerformanceMetrics object containing all computed metrics

        Example:
            >>> generator = ReportGenerator(account_df, positions, orders, fills, config)
            >>> metrics = generator.generate_report()
            >>> print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
            >>> print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%")

        """
        # Get risk-free rate from config if available
        risk_free_rate = getattr(self.config.metrics, "risk_free_rate", 0.04)

        # Compute all metric categories
        returns = self.compute_returns_metrics()
        drawdown = self.compute_drawdown()
        trades = self.compute_trade_metrics()
        execution = self.compute_execution_metrics()
        funding = self.compute_funding_metrics()
        exposure = self.compute_exposure_metrics()
        ladder = self.compute_ladder_utilization()
        mae_mfe = self.compute_mae_mfe()

        # Create comprehensive metrics object
        metrics = PerformanceMetrics(
            # Returns
            total_pnl=returns["total_pnl"],
            total_return_pct=returns["total_return_pct"],
            annualized_return_pct=returns["annualized_return_pct"],
            # Risk
            sharpe_ratio=self.compute_sharpe_ratio(risk_free_rate),
            sortino_ratio=self.compute_sortino_ratio(risk_free_rate),
            calmar_ratio=self.compute_calmar_ratio(),
            max_drawdown_pct=drawdown["max_drawdown_pct"],
            max_drawdown_duration_days=drawdown["max_drawdown_duration_days"],
            # Trades
            total_trades=trades["total_trades"],
            winning_trades=trades["winning_trades"],
            losing_trades=trades["losing_trades"],
            win_rate_pct=trades["win_rate_pct"],
            avg_win=trades["avg_win"],
            avg_loss=trades["avg_loss"],
            profit_factor=trades["profit_factor"],
            avg_trade_pnl=trades["avg_trade_pnl"],
            # Execution
            maker_fill_ratio=execution["maker_fill_ratio"],
            avg_slippage_bps=execution["avg_slippage_bps"],
            total_fees_paid=execution["total_fees_paid"],
            # Funding
            funding_paid=funding["funding_paid"],
            funding_received=funding["funding_received"],
            net_funding_pnl=funding["net_funding_pnl"],
            # Exposure
            avg_long_exposure=exposure["avg_long_exposure"],
            avg_short_exposure=exposure["avg_short_exposure"],
            max_long_exposure=exposure["max_long_exposure"],
            max_short_exposure=exposure["max_short_exposure"],
            time_in_market_pct=exposure["time_in_market_pct"],
            # Ladder
            avg_ladder_depth_long=ladder["avg_ladder_depth_long"],
            avg_ladder_depth_short=ladder["avg_ladder_depth_short"],
            ladder_fill_rate_pct=ladder["ladder_fill_rate_pct"],
            # MAE/MFE
            avg_mae_pct=mae_mfe["avg_mae_pct"],
            avg_mfe_pct=mae_mfe["avg_mfe_pct"],
        )

        return metrics
