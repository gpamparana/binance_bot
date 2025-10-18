"""
CSV file data source implementation.

Reads market data from CSV files with flexible column mapping
and automatic format detection.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from naut_hedgegrid.data.sources.base import DataSource

logger = logging.getLogger(__name__)


class CSVDataSource(DataSource):
    """
    CSV file data source.

    Reads market data from CSV files with configurable column mappings.
    Supports compressed files (.gz, .bz2) and flexible timestamp parsing.

    Attributes
    ----------
    config : dict
        Configuration mapping for different data types
    base_path : Path
        Base directory for CSV files

    """

    # Default column mappings for common CSV formats
    DEFAULT_TRADE_COLUMNS = {
        "timestamp": ["timestamp", "time", "datetime", "date"],
        "price": ["price", "close", "last"],
        "size": ["size", "volume", "quantity", "amount", "qty"],
        "aggressor_side": ["side", "aggressor_side", "taker_side"],
        "trade_id": ["trade_id", "id", "tid"],
    }

    DEFAULT_MARK_COLUMNS = {
        "timestamp": ["timestamp", "time", "datetime", "date"],
        "mark_price": ["mark_price", "mark", "price"],
    }

    DEFAULT_FUNDING_COLUMNS = {
        "timestamp": ["timestamp", "time", "datetime", "date"],
        "funding_rate": ["funding_rate", "rate", "funding"],
        "next_funding_time": ["next_funding_time", "next_funding", "funding_time"],
    }

    def __init__(self, config: dict[str, Any], base_path: str = ".") -> None:
        """
        Initialize CSV data source.

        Parameters
        ----------
        config : dict
            Configuration with file paths and column mappings:
            {
                "trades": {
                    "file_path": "trades.csv",
                    "columns": {...},  # Optional column mapping
                    "timestamp_format": "%Y-%m-%d %H:%M:%S"  # Optional
                },
                "mark": {...},
                "funding": {...}
            }
        base_path : str, default "."
            Base directory for resolving relative file paths

        """
        self.config = config
        self.base_path = Path(base_path)
        logger.info(f"Initialized CSVDataSource with base_path={base_path}")

    async def fetch_trades(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch trade data from CSV file.

        Parameters
        ----------
        symbol : str
            Trading symbol (used to filter data if CSV contains multiple symbols)
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
            raise ValueError("No trades configuration found in CSV config")

        trade_config = self.config["trades"]
        file_path = self.base_path / trade_config["file_path"]

        logger.info(f"Reading trades from {file_path}")
        df = self._read_csv(file_path, trade_config.get("timestamp_format"))

        # Map columns to standard schema
        column_mapping = trade_config.get("columns", {})
        df = self._map_columns(df, column_mapping, self.DEFAULT_TRADE_COLUMNS)

        # Ensure required columns exist
        required = ["timestamp", "price", "size", "aggressor_side", "trade_id"]
        missing = set(required) - set(df.columns)
        if missing:
            raise ValueError(f"Missing required trade columns: {missing}")

        # Filter by time range
        df = self._filter_timerange(df, start, end)

        # Filter by symbol if symbol column exists
        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == symbol.upper()]

        # Select and order columns
        df = df[required].copy()

        logger.info(f"Loaded {len(df):,} trades")
        return df

    async def fetch_mark_prices(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Fetch mark price data from CSV file.

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
            raise ValueError("No mark price configuration found in CSV config")

        mark_config = self.config["mark"]
        file_path = self.base_path / mark_config["file_path"]

        logger.info(f"Reading mark prices from {file_path}")
        df = self._read_csv(file_path, mark_config.get("timestamp_format"))

        # Map columns
        column_mapping = mark_config.get("columns", {})
        df = self._map_columns(df, column_mapping, self.DEFAULT_MARK_COLUMNS)

        # Ensure required columns
        required = ["timestamp", "mark_price"]
        missing = set(required) - set(df.columns)
        if missing:
            raise ValueError(f"Missing required mark price columns: {missing}")

        # Filter by time range
        df = self._filter_timerange(df, start, end)

        # Filter by symbol if exists
        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == symbol.upper()]

        df = df[required].copy()

        logger.info(f"Loaded {len(df):,} mark prices")
        return df

    async def fetch_funding_rates(
        self, symbol: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """
        Fetch funding rate data from CSV file.

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
            raise ValueError("No funding rate configuration found in CSV config")

        funding_config = self.config["funding"]
        file_path = self.base_path / funding_config["file_path"]

        logger.info(f"Reading funding rates from {file_path}")
        df = self._read_csv(file_path, funding_config.get("timestamp_format"))

        # Map columns
        column_mapping = funding_config.get("columns", {})
        df = self._map_columns(df, column_mapping, self.DEFAULT_FUNDING_COLUMNS)

        # Ensure required columns
        required = ["timestamp", "funding_rate"]
        if "next_funding_time" not in df.columns:
            df["next_funding_time"] = None

        # Filter by time range
        df = self._filter_timerange(df, start, end)

        # Filter by symbol if exists
        if "symbol" in df.columns:
            df = df[df["symbol"].str.upper() == symbol.upper()]

        df = df[required + ["next_funding_time"]].copy()

        logger.info(f"Loaded {len(df):,} funding rates")
        return df

    def _read_csv(self, file_path: Path, timestamp_format: str | None = None) -> pd.DataFrame:
        """
        Read CSV file with automatic compression detection.

        Parameters
        ----------
        file_path : Path
            Path to CSV file
        timestamp_format : str, optional
            Custom timestamp format string

        Returns
        -------
        pd.DataFrame
            Loaded DataFrame

        """
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # Detect compression
        compression = None
        if file_path.suffix == ".gz":
            compression = "gzip"
        elif file_path.suffix == ".bz2":
            compression = "bz2"

        # Read CSV
        df = pd.read_csv(file_path, compression=compression)

        logger.debug(f"Loaded CSV with {len(df)} rows, columns: {list(df.columns)}")
        return df

    def _map_columns(
        self,
        df: pd.DataFrame,
        user_mapping: dict[str, str],
        default_mapping: dict[str, list[str]],
    ) -> pd.DataFrame:
        """
        Map DataFrame columns to standard schema.

        Parameters
        ----------
        df : pd.DataFrame
            Input DataFrame
        user_mapping : dict
            User-provided column mapping
        default_mapping : dict
            Default column name alternatives

        Returns
        -------
        pd.DataFrame
            DataFrame with mapped columns

        """
        df = df.copy()
        df.columns = df.columns.str.lower().str.strip()

        # Apply user mapping first
        if user_mapping:
            rename_map = {v.lower(): k for k, v in user_mapping.items()}
            df = df.rename(columns=rename_map)

        # Try default mappings for missing columns
        for target_col, possible_names in default_mapping.items():
            if target_col not in df.columns:
                # Find first matching column
                for name in possible_names:
                    if name.lower() in df.columns:
                        df = df.rename(columns={name.lower(): target_col})
                        logger.debug(f"Auto-mapped column {name} -> {target_col}")
                        break

        return df

    def _filter_timerange(self, df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
        """
        Filter DataFrame by time range.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame with 'timestamp' column
        start : datetime
            Start timestamp (inclusive)
        end : datetime
            End timestamp (exclusive)

        Returns
        -------
        pd.DataFrame
            Filtered DataFrame

        """
        # Convert timestamp column to datetime
        if "timestamp" not in df.columns:
            raise ValueError("No timestamp column found in DataFrame")

        # Try parsing timestamp
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            # Try multiple formats
            for fmt in [None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "ISO8601"]:
                try:
                    if fmt is None:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                    else:
                        df["timestamp"] = pd.to_datetime(df["timestamp"], format=fmt)
                    break
                except Exception:
                    continue

        # Ensure UTC timezone
        if df["timestamp"].dt.tz is None:
            df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
        else:
            df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")

        # Filter
        start_utc = start if start.tzinfo else start.replace(tzinfo=UTC)
        end_utc = end if end.tzinfo else end.replace(tzinfo=UTC)

        mask = (df["timestamp"] >= start_utc) & (df["timestamp"] < end_utc)
        df = df[mask].copy()

        return df

    def __repr__(self) -> str:
        """Return string representation."""
        return f"CSVDataSource(base_path={self.base_path})"
