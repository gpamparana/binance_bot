"""
Shared pytest fixtures for data pipeline tests.

Provides reusable test data, mock objects, and helper functions for
testing data validation, normalization, and pipeline orchestration.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_trades_df():
    """
    Sample valid trade DataFrame.

    Returns
    -------
    pd.DataFrame
        Valid trade data with all required columns
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:00:01"], utc=True),
            "price": [50000.0, 50001.0],
            "size": [0.1, 0.2],
            "aggressor_side": ["BUY", "SELL"],
            "trade_id": ["12345", "12346"],
        }
    )


@pytest.fixture
def sample_mark_prices_df():
    """
    Sample valid mark price DataFrame (simple format).

    Returns
    -------
    pd.DataFrame
        Valid mark price data with timestamp and mark_price columns
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:00:05"], utc=True),
            "mark_price": [50001.0, 50002.0],
        }
    )


@pytest.fixture
def sample_mark_prices_ohlcv_df():
    """
    Sample valid mark price OHLCV DataFrame for bar conversion.

    Returns
    -------
    pd.DataFrame
        Valid mark price OHLCV data for pipeline bar conversion
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:01:00"], utc=True),
            "open": [50000.0, 50001.0],
            "high": [50010.0, 50012.0],
            "low": [49990.0, 49995.0],
            "close": [50001.0, 50002.0],
            "volume": [100.0, 150.0],
        }
    )


@pytest.fixture
def sample_funding_rates_df():
    """
    Sample valid funding rate DataFrame.

    Returns
    -------
    pd.DataFrame
        Valid funding rate data with all required columns
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 08:00:00"], utc=True),
            "funding_rate": [0.0001, -0.0002],
            "next_funding_time": pd.to_datetime(
                ["2024-01-01 08:00:00", "2024-01-01 16:00:00"], utc=True
            ),
        }
    )


@pytest.fixture
def invalid_trades_df():
    """
    Sample invalid trade DataFrame for negative testing.

    Contains negative price which should fail validation.

    Returns
    -------
    pd.DataFrame
        Invalid trade data
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
            "price": [-50000.0],  # Invalid: negative
            "size": [0.1],
            "aggressor_side": ["BUY"],
            "trade_id": ["12345"],
        }
    )


@pytest.fixture
def trades_df_with_invalid_side():
    """
    Trade DataFrame with invalid aggressor_side values.

    Returns
    -------
    pd.DataFrame
        Trade data with invalid side values
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
            "price": [50000.0],
            "size": [0.1],
            "aggressor_side": ["INVALID"],  # Invalid: not BUY or SELL
            "trade_id": ["12345"],
        }
    )


@pytest.fixture
def trades_df_with_zero_values():
    """
    Trade DataFrame with zero price/size values.

    Returns
    -------
    pd.DataFrame
        Trade data with zero values
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00:00", "2024-01-01 00:00:01"], utc=True),
            "price": [0.0, 50000.0],  # First row has zero price
            "size": [0.1, 0.0],  # Second row has zero size
            "aggressor_side": ["BUY", "SELL"],
            "trade_id": ["12345", "12346"],
        }
    )


@pytest.fixture
def large_trades_df():
    """
    Large trade DataFrame for performance testing.

    Returns
    -------
    pd.DataFrame
        Large dataset with 10,000 trades
    """
    np.random.seed(42)
    num_rows = 10_000

    timestamps = pd.date_range("2024-01-01", periods=num_rows, freq="1s", tz="UTC")

    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "price": np.random.uniform(45000, 55000, num_rows),
            "size": np.random.uniform(0.001, 1.0, num_rows),
            "aggressor_side": np.random.choice(["BUY", "SELL"], num_rows),
            "trade_id": [f"trade_{i}" for i in range(num_rows)],
        }
    )


@pytest.fixture
def trades_df_with_duplicates():
    """
    Trade DataFrame with duplicate timestamps and trade_ids.

    Returns
    -------
    pd.DataFrame
        Trade data with duplicates
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2024-01-01 00:00:00",
                    "2024-01-01 00:00:00",  # Duplicate timestamp
                    "2024-01-01 00:00:01",
                ],
                utc=True,
            ),
            "price": [50000.0, 50000.0, 50001.0],
            "size": [0.1, 0.1, 0.2],
            "aggressor_side": ["BUY", "BUY", "SELL"],
            "trade_id": ["12345", "12345", "12346"],  # Duplicate trade_id
        }
    )


@pytest.fixture
def trades_df_unsorted():
    """
    Trade DataFrame with unsorted timestamps.

    Returns
    -------
    pd.DataFrame
        Trade data with unsorted timestamps
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2024-01-01 00:00:02",
                    "2024-01-01 00:00:00",  # Out of order
                    "2024-01-01 00:00:01",
                ],
                utc=True,
            ),
            "price": [50002.0, 50000.0, 50001.0],
            "size": [0.3, 0.1, 0.2],
            "aggressor_side": ["SELL", "BUY", "SELL"],
            "trade_id": ["12347", "12345", "12346"],
        }
    )


@pytest.fixture
def start_date():
    """
    Standard start date for testing.

    Returns
    -------
    datetime
        2024-01-01 00:00:00 UTC
    """
    return datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture
def end_date():
    """
    Standard end date for testing.

    Returns
    -------
    datetime
        2024-01-03 00:00:00 UTC
    """
    return datetime(2024, 1, 3, tzinfo=UTC)


@pytest.fixture
def symbol():
    """
    Standard test symbol.

    Returns
    -------
    str
        "BTCUSDT"
    """
    return "BTCUSDT"
