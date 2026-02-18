"""Binance data warmer for fetching historical klines data.

This module provides functionality to fetch historical bar data from Binance
before live trading starts, enabling strategy components like regime detectors
to be pre-warmed with sufficient data.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from rich.console import Console

from naut_hedgegrid.config.venue import VenueConfig
from naut_hedgegrid.strategy.detector import Bar as DetectorBar


class BinanceDataWarmer:
    """
    Fetches historical klines (bar) data from Binance API for strategy warmup.

    This class handles:
    - Fetching historical klines from either testnet or production Binance
    - Converting klines to NautilusTrader Bar objects
    - Rate limiting and error handling
    - Supporting different bar intervals (1m, 5m, 15m, etc.)
    """

    # Binance API endpoints
    PROD_BASE_URL = "https://fapi.binance.com"
    TESTNET_BASE_URL = "https://testnet.binancefuture.com"
    KLINES_ENDPOINT = "/fapi/v1/klines"

    # Rate limiting
    MAX_KLINES_PER_REQUEST = 500  # Binance limit
    REQUEST_DELAY = 0.2  # Delay between requests to avoid rate limits

    # Bar interval mapping
    INTERVAL_MAP = {
        "1m": "1m",
        "1-MINUTE": "1m",
        "5m": "5m",
        "5-MINUTE": "5m",
        "15m": "15m",
        "15-MINUTE": "15m",
        "1h": "1h",
        "1-HOUR": "1h",
        "4h": "4h",
        "4-HOUR": "4h",
        "1d": "1d",
        "1-DAY": "1d",
    }

    def __init__(self, venue_config: VenueConfig, console: Console | None = None):
        """
        Initialize the Binance data warmer.

        Parameters
        ----------
        venue_config : VenueConfig
            Venue configuration containing API settings and testnet flag
        console : Console | None
            Rich console for logging (optional)
        """
        self.venue_config = venue_config
        self.console = console or Console()

        # Determine base URL based on testnet flag
        if venue_config.api.testnet:
            self.base_url = self.TESTNET_BASE_URL
            self.console.print("[yellow]Using Binance Testnet for data warmup[/yellow]")
        else:
            self.base_url = self.PROD_BASE_URL
            self.console.print("[green]Using Binance Production for data warmup[/green]")

        # Create HTTP client with timeout
        self.client = httpx.Client(timeout=30.0)

    def fetch_historical_bars(
        self,
        symbol: str,
        bar_type: BarType,
        num_bars: int = 100,
        end_time: datetime | None = None,
    ) -> list[Bar]:
        """
        Fetch historical bars from Binance API.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "BTCUSDT")
        bar_type : BarType
            NautilusTrader BarType specifying the bar specification
        num_bars : int
            Number of bars to fetch (default 100)
        end_time : datetime | None
            End time for historical data (default: now)

        Returns
        -------
        list[Bar]
            List of NautilusTrader Bar objects, ordered from oldest to newest

        Raises
        ------
        Exception
            If API request fails or returns invalid data
        """
        if end_time is None:
            end_time = datetime.now(UTC)

        # Extract interval from bar spec
        bar_spec = bar_type.spec
        interval_key = f"{bar_spec.step}-{bar_spec.aggregation.name}"
        interval = self.INTERVAL_MAP.get(interval_key, "1m")

        bars: list[Bar] = []
        remaining = num_bars

        self.console.print(f"[cyan]Fetching {num_bars} historical {interval} bars for {symbol}...[/cyan]")

        while remaining > 0:
            # Calculate request parameters
            limit = min(remaining, self.MAX_KLINES_PER_REQUEST)

            # Build request parameters
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            }

            # Add end time if we have bars already (pagination)
            if bars:
                # Set end time to just before the first bar we have
                first_bar = bars[0]
                end_ms = int(first_bar.ts_event / 1_000_000) - 1  # Convert nanos to millis
                params["endTime"] = end_ms
            else:
                # First request - use provided end time
                params["endTime"] = int(end_time.timestamp() * 1000)

            # Make API request
            try:
                response = self.client.get(
                    f"{self.base_url}{self.KLINES_ENDPOINT}",
                    params=params,
                )
                response.raise_for_status()
                klines = response.json()
            except httpx.HTTPError as e:
                error_msg = f"Failed to fetch klines from Binance: {e}"
                self.console.print(f"[red]{error_msg}[/red]")
                raise Exception(error_msg) from e

            if not klines:
                self.console.print("[yellow]No more historical data available[/yellow]")
                break

            # Convert klines to Bar objects (prepend to maintain order)
            new_bars = []
            for kline in klines:
                bar = self._kline_to_bar(kline, bar_type)
                new_bars.append(bar)

            # Prepend new bars (they're older than existing ones)
            bars = new_bars + bars
            remaining -= len(new_bars)

            self.console.print(f"[green]Fetched {len(new_bars)} bars, total: {len(bars)}/{num_bars}[/green]")

            # Rate limiting
            if remaining > 0:
                time.sleep(self.REQUEST_DELAY)

        self.console.print(f"[green]✓ Successfully fetched {len(bars)} historical bars[/green]")

        return bars

    def fetch_detector_bars(
        self,
        symbol: str,
        num_bars: int = 60,
        interval: str = "1m",
        end_time: datetime | None = None,
    ) -> list[DetectorBar]:
        """
        Fetch historical bars as DetectorBar objects for RegimeDetector warmup.

        This is a convenience method that fetches bars and converts them to
        the DetectorBar format used by the RegimeDetector.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g., "BTCUSDT")
        num_bars : int
            Number of bars to fetch (default 60)
        interval : str
            Bar interval (default "1m")
        end_time : datetime | None
            End time for historical data (default: now)

        Returns
        -------
        list[DetectorBar]
            List of DetectorBar objects for regime detector warmup
        """
        if end_time is None:
            end_time = datetime.now(UTC)

        bars: list[DetectorBar] = []
        remaining = num_bars

        self.console.print(f"[cyan]Fetching {num_bars} detector bars ({interval}) for {symbol}...[/cyan]")

        while remaining > 0:
            limit = min(remaining, self.MAX_KLINES_PER_REQUEST)

            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            }

            if bars:
                # Pagination: get older data
                # Use timestamp of oldest bar we have
                bars[0]
                # Approximate timestamp (since DetectorBar doesn't have timestamps)
                # We'll just request the next batch
                params["limit"] = limit
                # For pagination, we need to track end time differently
                # Since DetectorBar has no timestamp, we estimate based on interval
                interval_minutes = self._interval_to_minutes(interval)
                end_time = end_time - timedelta(minutes=interval_minutes * len(bars))
                params["endTime"] = int(end_time.timestamp() * 1000)
            else:
                params["endTime"] = int(end_time.timestamp() * 1000)

            try:
                response = self.client.get(
                    f"{self.base_url}{self.KLINES_ENDPOINT}",
                    params=params,
                )
                response.raise_for_status()
                klines = response.json()
            except httpx.HTTPError as e:
                error_msg = f"Failed to fetch detector bars: {e}"
                self.console.print(f"[red]{error_msg}[/red]")
                raise Exception(error_msg) from e

            if not klines:
                self.console.print("[yellow]No more historical data available[/yellow]")
                break

            # Convert to DetectorBar objects
            new_bars = []
            for kline in klines:
                detector_bar = self._kline_to_detector_bar(kline)
                new_bars.append(detector_bar)

            # Prepend (older data goes first)
            bars = new_bars + bars
            remaining -= len(new_bars)

            if remaining > 0:
                time.sleep(self.REQUEST_DELAY)

        self.console.print(f"[green]✓ Fetched {len(bars)} detector bars[/green]")

        return bars

    def _kline_to_bar(self, kline: list, bar_type: BarType) -> Bar:
        """
        Convert Binance kline data to NautilusTrader Bar.

        Binance kline format:
        [
            open_time,        # 0
            open,            # 1
            high,            # 2
            low,             # 3
            close,           # 4
            volume,          # 5
            close_time,      # 6
            quote_volume,    # 7
            trades_count,    # 8
            taker_buy_volume, # 9
            taker_buy_quote, # 10
            ignore           # 11
        ]
        """
        open_time_ms = kline[0]
        open_price = float(kline[1])
        high_price = float(kline[2])
        low_price = float(kline[3])
        close_price = float(kline[4])
        volume = float(kline[5])
        close_time_ms = kline[6]

        # Convert timestamps to nanoseconds
        ts_event = open_time_ms * 1_000_000  # millis to nanos
        ts_init = close_time_ms * 1_000_000

        return Bar(
            bar_type=bar_type,
            open=Price.from_str(str(open_price)),
            high=Price.from_str(str(high_price)),
            low=Price.from_str(str(low_price)),
            close=Price.from_str(str(close_price)),
            volume=Quantity.from_str(str(volume)),
            ts_event=ts_event,
            ts_init=ts_init,
        )

    def _kline_to_detector_bar(self, kline: list) -> DetectorBar:
        """
        Convert Binance kline data to DetectorBar for regime detector.

        Parameters
        ----------
        kline : list
            Raw kline data from Binance API

        Returns
        -------
        DetectorBar
            Bar object for regime detector
        """
        return DetectorBar(
            open=float(kline[1]),
            high=float(kline[2]),
            low=float(kline[3]),
            close=float(kline[4]),
            volume=float(kline[5]),
        )

    def _interval_to_minutes(self, interval: str) -> int:
        """
        Convert interval string to minutes.

        Parameters
        ----------
        interval : str
            Interval string (e.g., "1m", "5m", "1h")

        Returns
        -------
        int
            Number of minutes
        """
        if interval.endswith("m"):
            return int(interval[:-1])
        if interval.endswith("h"):
            return int(interval[:-1]) * 60
        if interval.endswith("d"):
            return int(interval[:-1]) * 1440
        return 1  # Default to 1 minute

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def __enter__(self) -> "BinanceDataWarmer":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()
