"""
Tardis.dev data source implementation.

Provides historical cryptocurrency market data via the Tardis.dev API.
Supports multiple exchanges and data types with automatic caching.
"""

import logging
import os
from datetime import UTC, datetime

import pandas as pd

from naut_hedgegrid.data.sources.base import DataSource

logger = logging.getLogger(__name__)


class TardisDataSource(DataSource):
    """
    Tardis.dev API data source.

    Fetches historical market data from Tardis.dev's replay API.
    Requires TARDIS_API_KEY environment variable for authentication.

    Attributes
    ----------
    api_key : str
        Tardis.dev API key
    exchange : str
        Exchange identifier (default: "binance-futures")
    cache_dir : str | None
        Directory for caching downloaded data

    """

    def __init__(
        self,
        api_key: str | None = None,
        exchange: str = "binance-futures",
        cache_dir: str | None = None,
    ) -> None:
        """
        Initialize Tardis data source.

        Parameters
        ----------
        api_key : str, optional
            Tardis API key (reads from TARDIS_API_KEY env if not provided)
        exchange : str, default "binance-futures"
            Exchange identifier
        cache_dir : str, optional
            Cache directory for downloaded data

        Raises
        ------
        ValueError
            If API key is not provided and TARDIS_API_KEY env var is not set

        """
        self.api_key = api_key or os.getenv("TARDIS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Tardis API key required. Set TARDIS_API_KEY environment variable or pass api_key parameter."
            )

        self.exchange = exchange
        self.cache_dir = cache_dir

        # Import tardis-client lazily
        try:
            from tardis_client import TardisClient  # type: ignore

            self.client = TardisClient(api_key=self.api_key, cache_dir=cache_dir)
        except ImportError as e:
            raise ImportError("tardis-client not installed. Install with: pip install tardis-client") from e

        logger.info(f"Initialized TardisDataSource for {exchange}")

    async def fetch_trades(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch trade data from Tardis.dev.

        Uses the aggTrade channel for Binance Futures which provides
        aggregated trade data with microsecond timestamps.

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

        """
        from tardis_client import Channel  # type: ignore

        logger.info(f"Fetching trades for {symbol} from {start} to {end}")

        # Convert to UTC if needed
        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        # Replay data from Tardis
        messages = self.client.replay(
            exchange=self.exchange,
            from_date=start_utc.strftime("%Y-%m-%d"),
            to_date=end_utc.strftime("%Y-%m-%d"),
            filters=[Channel(name="aggTrade", symbols=[symbol.lower()])],
        )

        # Parse messages
        trades = []
        for message in messages:
            if "data" not in message:
                continue

            data = message["data"]

            # Binance aggTrade format:
            # {
            #   "e": "aggTrade",
            #   "E": 1640995200000,  # Event time
            #   "s": "BTCUSDT",
            #   "a": 123456,         # Aggregate trade ID
            #   "p": "47000.00",     # Price
            #   "q": "0.001",        # Quantity
            #   "f": 1,              # First trade ID
            #   "l": 1,              # Last trade ID
            #   "T": 1640995200000,  # Trade time
            #   "m": true            # Is buyer maker
            # }

            if data.get("e") != "aggTrade":
                continue

            # Parse timestamp (milliseconds to datetime)
            ts_ms = data.get("T", data.get("E", 0))
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            # Filter by time range
            if timestamp < start_utc or timestamp >= end_utc:
                continue

            # Parse fields
            price = float(data["p"])
            size = float(data["q"])
            trade_id = str(data["a"])

            # Determine aggressor side
            # m=true means buyer is maker, so seller is aggressor (SELL)
            # m=false means buyer is taker, so buyer is aggressor (BUY)
            is_buyer_maker = data.get("m", False)
            aggressor_side = "SELL" if is_buyer_maker else "BUY"

            trades.append(
                {
                    "timestamp": timestamp,
                    "price": price,
                    "size": size,
                    "aggressor_side": aggressor_side,
                    "trade_id": trade_id,
                }
            )

        if not trades:
            logger.warning(f"No trades found for {symbol} in time range")
            return pd.DataFrame(columns=["timestamp", "price", "size", "aggressor_side", "trade_id"])

        df = pd.DataFrame(trades)
        logger.info(f"Fetched {len(df):,} trades for {symbol}")
        return df

    async def fetch_mark_prices(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mark price data from Tardis.dev.

        Uses the markPrice@1s channel for 1-second mark price updates.

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

        """
        from tardis_client import Channel  # type: ignore

        logger.info(f"Fetching mark prices for {symbol} from {start} to {end}")

        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        messages = self.client.replay(
            exchange=self.exchange,
            from_date=start_utc.strftime("%Y-%m-%d"),
            to_date=end_utc.strftime("%Y-%m-%d"),
            filters=[Channel(name="markPrice@1s", symbols=[symbol.lower()])],
        )

        # Parse messages
        mark_prices = []
        for message in messages:
            if "data" not in message:
                continue

            data = message["data"]

            # Binance markPrice format:
            # {
            #   "e": "markPriceUpdate",
            #   "E": 1640995201000,  # Event time
            #   "s": "BTCUSDT",
            #   "p": "47001.50",     # Mark price
            #   "i": "47001.00",     # Index price
            #   "P": "47002.00",     # Estimated settle price
            #   "r": "0.0001",       # Funding rate
            #   "T": 1640995201000   # Next funding time
            # }

            if data.get("e") != "markPriceUpdate":
                continue

            ts_ms = data.get("E", 0)
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            if timestamp < start_utc or timestamp >= end_utc:
                continue

            mark_price = float(data["p"])

            mark_prices.append({"timestamp": timestamp, "mark_price": mark_price})

        if not mark_prices:
            logger.warning(f"No mark prices found for {symbol} in time range")
            return pd.DataFrame(columns=["timestamp", "mark_price"])

        df = pd.DataFrame(mark_prices)
        logger.info(f"Fetched {len(df):,} mark prices for {symbol}")
        return df

    async def fetch_funding_rates(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch funding rate data from Tardis.dev.

        Uses the markPrice@1s channel which includes funding rate info.

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
            DataFrame with columns: timestamp, funding_rate, next_funding_time

        """
        from tardis_client import Channel  # type: ignore

        logger.info(f"Fetching funding rates for {symbol} from {start} to {end}")

        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        messages = self.client.replay(
            exchange=self.exchange,
            from_date=start_utc.strftime("%Y-%m-%d"),
            to_date=end_utc.strftime("%Y-%m-%d"),
            filters=[Channel(name="markPrice@1s", symbols=[symbol.lower()])],
        )

        # Parse messages
        funding_rates = []
        seen_timestamps = set()  # Deduplicate (funding rate updates less frequently)

        for message in messages:
            if "data" not in message:
                continue

            data = message["data"]

            if data.get("e") != "markPriceUpdate":
                continue

            ts_ms = data.get("E", 0)
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            if timestamp < start_utc or timestamp >= end_utc:
                continue

            # Deduplicate by rounding to nearest second
            ts_key = timestamp.floor("s")
            if ts_key in seen_timestamps:
                continue
            seen_timestamps.add(ts_key)

            funding_rate = float(data.get("r", 0.0))
            next_funding_ms = data.get("T", 0)
            next_funding_time = pd.Timestamp(next_funding_ms, unit="ms", tz="UTC") if next_funding_ms else None

            funding_rates.append(
                {
                    "timestamp": timestamp,
                    "funding_rate": funding_rate,
                    "next_funding_time": next_funding_time,
                }
            )

        if not funding_rates:
            logger.warning(f"No funding rates found for {symbol} in time range")
            return pd.DataFrame(columns=["timestamp", "funding_rate", "next_funding_time"])

        df = pd.DataFrame(funding_rates)
        logger.info(f"Fetched {len(df):,} funding rate updates for {symbol}")
        return df

    async def validate_connection(self) -> bool:
        """
        Validate Tardis API connection.

        Returns
        -------
        bool
            True if connection is valid

        Raises
        ------
        ConnectionError
            If API key is invalid or connection fails

        """
        try:
            # Test with a minimal query
            from tardis_client import Channel  # type: ignore

            list(
                self.client.replay(
                    exchange=self.exchange,
                    from_date="2024-01-01",
                    to_date="2024-01-01",
                    filters=[Channel(name="aggTrade", symbols=["btcusdt"])],
                )
            )
            logger.info("Tardis connection validated successfully")
            return True
        except Exception as e:
            logger.error(f"Tardis connection validation failed: {e}")
            raise ConnectionError(f"Failed to validate Tardis connection: {e}") from e

    def __repr__(self) -> str:
        """Return string representation."""
        return f"TardisDataSource(exchange={self.exchange})"
