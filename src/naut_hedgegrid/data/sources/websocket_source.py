"""
WebSocket capture replay data source.

Reads and parses JSONL files containing captured WebSocket messages
from cryptocurrency exchanges.
"""

import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from naut_hedgegrid.data.sources.base import DataSource

logger = logging.getLogger(__name__)


class WebSocketDataSource(DataSource):
    """
    WebSocket JSONL data source.

    Reads captured WebSocket messages from JSONL files (one JSON per line).
    Supports Binance Futures message formats.

    Attributes
    ----------
    config : dict
        Configuration with file paths and parsing options
    base_path : Path
        Base directory for JSONL files

    """

    def __init__(self, config: dict[str, Any], base_path: str = ".") -> None:
        """
        Initialize WebSocket data source.

        Parameters
        ----------
        config : dict
            Configuration with file paths:
            {
                "trades": {"file_path": "trades.jsonl"},
                "mark": {"file_path": "mark.jsonl"},
                "funding": {"file_path": "funding.jsonl"}
            }
        base_path : str, default "."
            Base directory for file paths

        """
        self.config = config
        self.base_path = Path(base_path)
        logger.info(f"Initialized WebSocketDataSource with base_path={base_path}")

    async def fetch_trades(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch trade data from JSONL file.

        Parses Binance Futures aggTrade messages.

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
        if "trades" not in self.config:
            raise ValueError("No trades configuration found in WebSocket config")

        file_path = self.base_path / self.config["trades"]["file_path"]
        logger.info(f"Reading trades from {file_path}")

        messages = self._read_jsonl(file_path)

        # Parse aggTrade messages
        trades = []
        for msg in messages:
            # Handle both wrapped and unwrapped formats
            data = msg.get("data", msg)

            if data.get("e") != "aggTrade":
                continue

            # Check symbol match
            msg_symbol = data.get("s", "").upper()
            if msg_symbol != symbol.upper():
                continue

            # Parse timestamp
            ts_ms = data.get("T", data.get("E", 0))
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            # Filter by time range
            start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
            end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

            if timestamp < start_utc or timestamp >= end_utc:
                continue

            # Parse fields
            price = float(data["p"])
            size = float(data["q"])
            trade_id = str(data["a"])

            # Determine aggressor side
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
            logger.warning(f"No trades found for {symbol}")
            return pd.DataFrame(
                columns=["timestamp", "price", "size", "aggressor_side", "trade_id"]
            )

        df = pd.DataFrame(trades)
        logger.info(f"Loaded {len(df):,} trades")
        return df

    async def fetch_mark_prices(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mark price data from JSONL file.

        Parses Binance Futures markPriceUpdate messages.

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
            DataFrame with columns: timestamp, mark_price

        """
        if "mark" not in self.config:
            raise ValueError("No mark price configuration found in WebSocket config")

        file_path = self.base_path / self.config["mark"]["file_path"]
        logger.info(f"Reading mark prices from {file_path}")

        messages = self._read_jsonl(file_path)

        # Parse markPriceUpdate messages
        mark_prices = []
        for msg in messages:
            data = msg.get("data", msg)

            if data.get("e") != "markPriceUpdate":
                continue

            # Check symbol
            msg_symbol = data.get("s", "").upper()
            if msg_symbol != symbol.upper():
                continue

            # Parse timestamp
            ts_ms = data.get("E", 0)
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            # Filter by time range
            start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
            end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

            if timestamp < start_utc or timestamp >= end_utc:
                continue

            mark_price = float(data["p"])

            mark_prices.append({"timestamp": timestamp, "mark_price": mark_price})

        if not mark_prices:
            logger.warning(f"No mark prices found for {symbol}")
            return pd.DataFrame(columns=["timestamp", "mark_price"])

        df = pd.DataFrame(mark_prices)
        logger.info(f"Loaded {len(df):,} mark prices")
        return df

    async def fetch_funding_rates(
        self, symbol: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """
        Fetch funding rate data from JSONL file.

        Extracts funding rate info from markPriceUpdate messages.

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
            DataFrame with columns: timestamp, funding_rate, next_funding_time

        """
        if "funding" not in self.config:
            # Try to extract from mark price messages
            if "mark" not in self.config:
                raise ValueError("No funding rate configuration found")
            file_path = self.base_path / self.config["mark"]["file_path"]
        else:
            file_path = self.base_path / self.config["funding"]["file_path"]

        logger.info(f"Reading funding rates from {file_path}")

        messages = self._read_jsonl(file_path)

        # Parse funding rate info
        funding_rates = []
        seen_timestamps = set()

        for msg in messages:
            data = msg.get("data", msg)

            if data.get("e") != "markPriceUpdate":
                continue

            # Check symbol
            msg_symbol = data.get("s", "").upper()
            if msg_symbol != symbol.upper():
                continue

            # Parse timestamp
            ts_ms = data.get("E", 0)
            timestamp = pd.Timestamp(ts_ms, unit="ms", tz="UTC")

            # Filter by time range
            start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
            end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

            if timestamp < start_utc or timestamp >= end_utc:
                continue

            # Deduplicate (funding rate updates infrequently)
            ts_key = timestamp.floor("s")
            if ts_key in seen_timestamps:
                continue
            seen_timestamps.add(ts_key)

            funding_rate = float(data.get("r", 0.0))
            next_funding_ms = data.get("T", 0)
            next_funding_time = (
                pd.Timestamp(next_funding_ms, unit="ms", tz="UTC") if next_funding_ms else None
            )

            funding_rates.append(
                {
                    "timestamp": timestamp,
                    "funding_rate": funding_rate,
                    "next_funding_time": next_funding_time,
                }
            )

        if not funding_rates:
            logger.warning(f"No funding rates found for {symbol}")
            return pd.DataFrame(columns=["timestamp", "funding_rate", "next_funding_time"])

        df = pd.DataFrame(funding_rates)
        logger.info(f"Loaded {len(df):,} funding rates")
        return df

    def _read_jsonl(self, file_path: Path) -> list[dict[str, Any]]:
        """
        Read JSONL file with compression support.

        Parameters
        ----------
        file_path : Path
            Path to JSONL file

        Returns
        -------
        list[dict]
            List of parsed JSON objects

        """
        if not file_path.exists():
            raise FileNotFoundError(f"JSONL file not found: {file_path}")

        messages = []

        # Handle compression
        if file_path.suffix == ".gz":
            open_func = gzip.open
            mode = "rt"
        else:
            open_func = open
            mode = "r"

        with open_func(file_path, mode) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line {line_num}: {e}")
                    continue

        logger.debug(f"Read {len(messages)} messages from {file_path}")
        return messages

    def __repr__(self) -> str:
        """Return string representation."""
        return f"WebSocketDataSource(base_path={self.base_path})"
