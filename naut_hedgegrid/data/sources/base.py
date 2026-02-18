"""
Abstract base class for market data sources.

Defines the interface that all data sources must implement to ensure
consistent data retrieval across different providers (Tardis, CSV, WebSocket, etc.).
"""

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class DataSource(ABC):
    """
    Abstract interface for market data sources.

    All data sources must implement methods to fetch different types
    of market data in standardized schema formats.

    Implementations should handle:
    - Authentication and connection management
    - Rate limiting and error handling
    - Data format conversion to standard schemas
    - Timestamp normalization to UTC
    """

    @abstractmethod
    async def fetch_trades(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch trade data in TradeSchema format.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "BTCUSDT")
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns: timestamp, price, size, aggressor_side, trade_id
            All timestamps in UTC timezone

        Raises
        ------
        ValueError
            If symbol is invalid or date range is invalid
        ConnectionError
            If unable to fetch data from source

        """

    @abstractmethod
    async def fetch_mark_prices(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mark price data in MarkPriceSchema format.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "BTCUSDT")
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns: timestamp, mark_price
            All timestamps in UTC timezone

        Raises
        ------
        ValueError
            If symbol is invalid or date range is invalid
        ConnectionError
            If unable to fetch data from source

        """

    @abstractmethod
    async def fetch_funding_rates(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch funding rate data in FundingRateSchema format.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "BTCUSDT")
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns: timestamp, funding_rate, next_funding_time (optional)
            All timestamps in UTC timezone

        Raises
        ------
        ValueError
            If symbol is invalid or date range is invalid
        ConnectionError
            If unable to fetch data from source

        """

    async def validate_connection(self) -> bool:
        """
        Validate that the data source is accessible.

        Returns
        -------
        bool
            True if connection is valid

        Raises
        ------
        ConnectionError
            If connection validation fails

        """
        # Default implementation - subclasses can override
        return True

    async def close(self) -> None:
        """
        Clean up resources and close connections.

        Subclasses should override this to properly close
        any open connections, files, or network resources.
        """
        # Default implementation - no-op

    def __repr__(self) -> str:
        """Return string representation of data source."""
        return f"{self.__class__.__name__}()"
