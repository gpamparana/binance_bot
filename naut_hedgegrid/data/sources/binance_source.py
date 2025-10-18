"""
Binance Futures data source for downloading historical market data.

Downloads data directly from Binance Futures REST API (no API key required for historical data).
Supports aggregated trades, mark prices, and funding rate history.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import pandas as pd

from naut_hedgegrid.data.sources.base import DataSource

logger = logging.getLogger(__name__)


class BinanceDataSource(DataSource):
    """
    Data source for Binance Futures historical data.

    Downloads market data directly from Binance Futures public REST API.
    No authentication required for historical data.

    Parameters
    ----------
    base_url : str, optional
        Binance Futures API base URL (default: https://fapi.binance.com)
    rate_limit_delay : float, optional
        Delay between requests in seconds (default: 0.2 = 5 req/sec)
    request_limit : int, optional
        Maximum records per request (default: 1000 - Binance limit)
    testnet : bool, optional
        Use testnet API (default: False)

    Examples
    --------
    >>> source = BinanceDataSource()
    >>> trades = await source.fetch_trades(
    ...     symbol="BTCUSDT",
    ...     start=datetime(2024, 1, 1, tzinfo=timezone.utc),
    ...     end=datetime(2024, 1, 2, tzinfo=timezone.utc)
    ... )

    """

    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        rate_limit_delay: float = 0.5,  # 2 req/sec = 120/min (5% of 2400/min limit - very conservative)
        request_limit: int = 500,  # Smaller batches = more requests but less data per request
        testnet: bool = False,
        max_retries: int = 5,
    ):
        """Initialize Binance data source."""
        self.base_url = (
            "https://testnet.binancefuture.com" if testnet else base_url
        )
        self.rate_limit_delay = rate_limit_delay
        self.request_limit = request_limit
        self.max_retries = max_retries
        self.session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any],
    ) -> list[Any]:
        """
        Make HTTP request to Binance API with exponential backoff.

        Parameters
        ----------
        endpoint : str
            API endpoint (e.g., "/fapi/v1/aggTrades")
        params : dict
            Query parameters

        Returns
        -------
        list
            JSON response data

        Raises
        ------
        ConnectionError
            If request fails after all retries

        """
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        for attempt in range(self.max_retries):
            try:
                async with session.get(url, params=params) as response:
                    # Handle rate limiting with exponential backoff
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        backoff_time = min(retry_after, 2 ** attempt * 10)
                        logger.warning(
                            f"Rate limit hit (429), waiting {backoff_time}s "
                            f"(attempt {attempt + 1}/{self.max_retries})"
                        )
                        await asyncio.sleep(backoff_time)
                        continue

                    if response.status != 200:
                        text = await response.text()
                        if attempt < self.max_retries - 1:
                            logger.warning(
                                f"Request failed with {response.status}: {text}, "
                                f"retrying (attempt {attempt + 1}/{self.max_retries})"
                            )
                            await asyncio.sleep(2 ** attempt)
                            continue
                        raise ConnectionError(
                            f"Binance API error {response.status}: {text}"
                        )

                    data = await response.json()

                    # Base rate limiting (0.5s = 2 req/sec = 120/min, 5% of 2400/min limit)
                    await asyncio.sleep(self.rate_limit_delay)

                    return data

            except aiohttp.ClientError as e:
                if attempt < self.max_retries - 1:
                    logger.warning(f"Network error: {e}, retrying...")
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise ConnectionError(f"Network error after {self.max_retries} retries: {e}")

        raise ConnectionError(f"Max retries ({self.max_retries}) exceeded")

    async def fetch_trades(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch aggregated trade data from Binance Futures.

        Uses /fapi/v1/aggTrades endpoint to download historical trades.
        Automatically handles pagination for large date ranges.

        Parameters
        ----------
        symbol : str
            Trading pair symbol (e.g., "BTCUSDT")
        start : datetime
            Start time (inclusive, UTC)
        end : datetime
            End time (exclusive, UTC)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns:
            - timestamp: datetime64[ns, UTC]
            - price: float
            - size: float (quantity in base asset)
            - aggressor_side: str ("BUY" or "SELL")
            - trade_id: str

        Raises
        ------
        ValueError
            If symbol or date range is invalid
        ConnectionError
            If API request fails

        Notes
        -----
        Binance API limits:
        - Max 1000 records per request
        - Rate limit: ~1200 requests/min (IP-based)
        - Historical data availability varies by symbol

        """
        if not symbol:
            raise ValueError("Symbol cannot be empty")
        if start >= end:
            raise ValueError("Start time must be before end time")

        # Convert to milliseconds
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        all_trades = []
        current_start = start_ms

        # Paginate through data
        while current_start < end_ms:
            params = {
                "symbol": symbol.upper(),
                "startTime": current_start,
                "endTime": end_ms,
                "limit": self.request_limit,
            }

            batch = await self._request("/fapi/v1/aggTrades", params)

            if not batch:
                break

            all_trades.extend(batch)

            # Update start time for next batch (last trade time + 1ms)
            current_start = batch[-1]["T"] + 1

            # Check if we've reached the end
            if len(batch) < self.request_limit:
                break

        if not all_trades:
            return pd.DataFrame(
                columns=["timestamp", "price", "size", "aggressor_side", "trade_id"]
            )

        # Convert to DataFrame
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [t["T"] for t in all_trades], unit="ms", utc=True
                ),
                "price": [float(t["p"]) for t in all_trades],
                "size": [float(t["q"]) for t in all_trades],
                "aggressor_side": [
                    "SELL" if t["m"] else "BUY" for t in all_trades
                ],  # m=true means buyer is maker
                "trade_id": [str(t["a"]) for t in all_trades],
            }
        )

        return df

    async def fetch_mark_prices(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch mark price history from Binance Futures.

        Uses /fapi/v1/markPriceKlines endpoint to download 1-minute mark price candles.

        Parameters
        ----------
        symbol : str
            Trading pair symbol (e.g., "BTCUSDT")
        start : datetime
            Start time (inclusive, UTC)
        end : datetime
            End time (exclusive, UTC)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns:
            - timestamp: datetime64[ns, UTC]
            - mark_price: float (close price of 1m candle)

        Raises
        ------
        ValueError
            If symbol or date range is invalid
        ConnectionError
            If API request fails

        Notes
        -----
        - Returns 1-minute mark price candles (uses close price)
        - Max 1500 candles per request
        - Mark prices are updated every second by Binance

        """
        if not symbol:
            raise ValueError("Symbol cannot be empty")
        if start >= end:
            raise ValueError("Start time must be before end time")

        # Convert to milliseconds
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        all_klines = []
        current_start = start_ms

        # Paginate through data (1500 candles per request)
        while current_start < end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": "1m",  # 1-minute candles
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1500,  # Binance max for klines
            }

            batch = await self._request("/fapi/v1/markPriceKlines", params)

            if not batch:
                break

            all_klines.extend(batch)

            # Update start time for next batch
            current_start = batch[-1][6] + 1  # Close time + 1ms

            # Check if we've reached the end
            if len(batch) < 1500:
                break

        if not all_klines:
            return pd.DataFrame(columns=["timestamp", "mark_price"])

        # Convert to DataFrame (use close price of candle)
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [k[0] for k in all_klines], unit="ms", utc=True
                ),  # Open time
                "mark_price": [float(k[4]) for k in all_klines],  # Close price
            }
        )

        return df

    async def fetch_funding_rates(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch funding rate history from Binance Futures.

        Uses /fapi/v1/fundingRate endpoint to download historical funding rates.

        Parameters
        ----------
        symbol : str
            Trading pair symbol (e.g., "BTCUSDT")
        start : datetime
            Start time (inclusive, UTC)
        end : datetime
            End time (exclusive, UTC)

        Returns
        -------
        pd.DataFrame
            DataFrame with columns:
            - timestamp: datetime64[ns, UTC] (funding time)
            - funding_rate: float
            - next_funding_time: datetime64[ns, UTC] (optional)

        Raises
        ------
        ValueError
            If symbol or date range is invalid
        ConnectionError
            If API request fails

        Notes
        -----
        - Funding occurs every 8 hours (00:00, 08:00, 16:00 UTC)
        - Max 1000 records per request
        - Historical data available from contract launch

        """
        if not symbol:
            raise ValueError("Symbol cannot be empty")
        if start >= end:
            raise ValueError("Start time must be before end time")

        # Convert to milliseconds
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        all_funding = []
        current_start = start_ms

        # Paginate through data
        while current_start < end_ms:
            params = {
                "symbol": symbol.upper(),
                "startTime": current_start,
                "endTime": end_ms,
                "limit": self.request_limit,
            }

            batch = await self._request("/fapi/v1/fundingRate", params)

            if not batch:
                break

            all_funding.extend(batch)

            # Update start time for next batch
            current_start = batch[-1]["fundingTime"] + 1

            # Check if we've reached the end
            if len(batch) < self.request_limit:
                break

        if not all_funding:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        # Convert to DataFrame
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [f["fundingTime"] for f in all_funding], unit="ms", utc=True
                ),
                "funding_rate": [float(f["fundingRate"]) for f in all_funding],
            }
        )

        # Calculate next funding time (8 hours after current)
        df["next_funding_time"] = df["timestamp"] + timedelta(hours=8)

        return df

    async def validate_connection(self) -> bool:
        """
        Validate Binance API connectivity.

        Returns
        -------
        bool
            True if API is accessible

        Raises
        ------
        ConnectionError
            If unable to reach Binance API

        """
        try:
            # Test with ping endpoint
            await self._request("/fapi/v1/ping", {})
            return True
        except Exception as e:
            raise ConnectionError(f"Unable to connect to Binance API: {e}")

    async def close(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None

    def __repr__(self) -> str:
        """Return string representation."""
        return f"BinanceDataSource(base_url='{self.base_url}')"
