"""
Mock implementations for data pipeline testing.

Provides MockDataSource for testing pipeline orchestration without
requiring real data sources or external dependencies.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from naut_hedgegrid.data.sources.base import DataSource


class MockDataSource(DataSource):
    """
    Mock data source for testing.

    Generates realistic sample data on demand for testing pipeline logic
    without requiring external data sources or files.

    Attributes
    ----------
    trades_df : pd.DataFrame
        Pre-generated trade data
    mark_df : pd.DataFrame
        Pre-generated mark price data
    funding_df : pd.DataFrame
        Pre-generated funding rate data
    should_fail : bool
        If True, fetch methods will raise errors
    """

    def __init__(
        self,
        trades_df: pd.DataFrame | None = None,
        mark_df: pd.DataFrame | None = None,
        funding_df: pd.DataFrame | None = None,
        should_fail: bool = False,
        num_trades: int = 1000,
        num_marks: int = 100,
        num_funding: int = 8,
    ) -> None:
        """
        Initialize mock data source.

        Parameters
        ----------
        trades_df : pd.DataFrame, optional
            Pre-defined trade data, if None will generate sample data
        mark_df : pd.DataFrame, optional
            Pre-defined mark price data, if None will generate sample data
        funding_df : pd.DataFrame, optional
            Pre-defined funding rate data, if None will generate sample data
        should_fail : bool, default False
            If True, fetch methods will raise ConnectionError
        num_trades : int, default 1000
            Number of trades to generate if trades_df is None
        num_marks : int, default 100
            Number of mark prices to generate if mark_df is None
        num_funding : int, default 8
            Number of funding rates to generate if funding_df is None
        """
        self.trades_df = (
            trades_df if trades_df is not None else self._generate_sample_trades(num_trades)
        )
        self.mark_df = mark_df if mark_df is not None else self._generate_sample_marks(num_marks)
        self.funding_df = (
            funding_df if funding_df is not None else self._generate_sample_funding(num_funding)
        )
        self.should_fail = should_fail
        self.fetch_count = {"trades": 0, "mark": 0, "funding": 0}

    async def fetch_trades(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mock trade data.

        Parameters
        ----------
        symbol : str
            Trading symbol (used for tracking, not filtering)
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            Trade data filtered by date range

        Raises
        ------
        ConnectionError
            If should_fail is True
        """
        if self.should_fail:
            raise ConnectionError("Mock connection failure")

        self.fetch_count["trades"] += 1

        # Filter by date range
        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        filtered = self.trades_df[
            (self.trades_df["timestamp"] >= start_utc) & (self.trades_df["timestamp"] < end_utc)
        ].copy()

        return filtered

    async def fetch_mark_prices(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mock mark price data.

        Parameters
        ----------
        symbol : str
            Trading symbol
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            Mark price data filtered by date range

        Raises
        ------
        ConnectionError
            If should_fail is True
        """
        if self.should_fail:
            raise ConnectionError("Mock connection failure")

        self.fetch_count["mark"] += 1

        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        filtered = self.mark_df[
            (self.mark_df["timestamp"] >= start_utc) & (self.mark_df["timestamp"] < end_utc)
        ].copy()

        return filtered

    async def fetch_funding_rates(
        self, symbol: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """
        Fetch mock funding rate data.

        Parameters
        ----------
        symbol : str
            Trading symbol
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            Funding rate data filtered by date range

        Raises
        ------
        ConnectionError
            If should_fail is True
        """
        if self.should_fail:
            raise ConnectionError("Mock connection failure")

        self.fetch_count["funding"] += 1

        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        filtered = self.funding_df[
            (self.funding_df["timestamp"] >= start_utc) & (self.funding_df["timestamp"] < end_utc)
        ].copy()

        return filtered

    async def validate_connection(self) -> bool:
        """
        Mock connection validation.

        Returns
        -------
        bool
            True unless should_fail is True

        Raises
        ------
        ConnectionError
            If should_fail is True
        """
        if self.should_fail:
            raise ConnectionError("Mock connection validation failed")
        return True

    @staticmethod
    def _generate_sample_trades(num_rows: int = 1000) -> pd.DataFrame:
        """
        Generate realistic sample trade data.

        Parameters
        ----------
        num_rows : int, default 1000
            Number of trades to generate

        Returns
        -------
        pd.DataFrame
            Generated trade data with realistic patterns
        """
        np.random.seed(42)

        # Generate timestamps over 2 days
        timestamps = pd.date_range("2024-01-01", periods=num_rows, freq="5s", tz="UTC")

        # Generate prices with realistic walk pattern
        base_price = 50000.0
        price_changes = np.random.normal(0, 10, num_rows)
        prices = base_price + np.cumsum(price_changes)

        # Generate sizes with realistic distribution (exponential)
        sizes = np.random.exponential(0.05, num_rows)
        sizes = np.clip(sizes, 0.001, 1.0)

        # Generate sides (roughly 50/50)
        sides = np.random.choice(["BUY", "SELL"], num_rows)

        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "price": prices,
                "size": sizes,
                "aggressor_side": sides,
                "trade_id": [f"trade_{i}" for i in range(num_rows)],
            }
        )

    @staticmethod
    def _generate_sample_marks(num_rows: int = 100) -> pd.DataFrame:
        """
        Generate realistic sample mark price data.

        Parameters
        ----------
        num_rows : int, default 100
            Number of mark prices to generate

        Returns
        -------
        pd.DataFrame
            Generated mark price data
        """
        np.random.seed(43)

        timestamps = pd.date_range("2024-01-01", periods=num_rows, freq="60s", tz="UTC")

        # Mark prices closely track spot with small premium/discount
        base_price = 50000.0
        price_changes = np.random.normal(0, 5, num_rows)
        mark_prices = base_price + np.cumsum(price_changes)

        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "mark_price": mark_prices,
            }
        )

    @staticmethod
    def _generate_sample_funding(num_rows: int = 8) -> pd.DataFrame:
        """
        Generate realistic sample funding rate data.

        Funding rates are updated every 8 hours on most exchanges.

        Parameters
        ----------
        num_rows : int, default 8
            Number of funding rate updates to generate

        Returns
        -------
        pd.DataFrame
            Generated funding rate data
        """
        np.random.seed(44)

        # Funding every 8 hours
        timestamps = pd.date_range("2024-01-01", periods=num_rows, freq="8h", tz="UTC")

        # Funding rates typically small, can be positive or negative
        funding_rates = np.random.normal(0.0001, 0.0002, num_rows)

        # Next funding time is 8 hours after current
        next_funding_times = timestamps + pd.Timedelta(hours=8)

        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "funding_rate": funding_rates,
                "next_funding_time": next_funding_times,
            }
        )

    def reset_counts(self) -> None:
        """Reset fetch count tracking."""
        self.fetch_count = {"trades": 0, "mark": 0, "funding": 0}

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"MockDataSource("
            f"trades={len(self.trades_df)}, "
            f"marks={len(self.mark_df)}, "
            f"funding={len(self.funding_df)})"
        )
