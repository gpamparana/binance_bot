"""
Tests for data normalization functions.

Tests timestamp conversion, data validation, sorting, deduplication,
and normalization of trades, mark prices, and funding rates from various
source formats.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from naut_hedgegrid.data.pipelines.normalizer import (
    _normalize_timestamp,
    normalize_funding_rates,
    normalize_mark_prices,
    normalize_trades,
)

# ============================================================================
# Timestamp Normalization Tests
# ============================================================================


class TestNormalizeTimestamp:
    """Tests for _normalize_timestamp function."""

    def test_normalize_iso_string_timestamps(self):
        """Test normalization of ISO format string timestamps."""
        ts_series = pd.Series(
            [
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:01Z",
            ]
        )

        result = _normalize_timestamp(ts_series)

        assert pd.api.types.is_datetime64_any_dtype(result)
        assert result.dt.tz is not None
        # Check each element has UTC timezone
        assert all(ts.tzinfo == UTC for ts in result)

    def test_normalize_unix_seconds(self):
        """Test normalization of Unix seconds timestamps."""
        # January 1, 2024 00:00:00 UTC = 1704067200 seconds
        ts_series = pd.Series([1704067200, 1704067201])

        result = _normalize_timestamp(ts_series)

        assert pd.api.types.is_datetime64_any_dtype(result)
        assert result.iloc[0].year == 2024
        assert result.iloc[0].month == 1
        assert result.iloc[0].day == 1

    def test_normalize_unix_milliseconds(self):
        """Test normalization of Unix milliseconds timestamps."""
        # Milliseconds
        ts_series = pd.Series([1704067200000, 1704067201000])

        result = _normalize_timestamp(ts_series)

        assert pd.api.types.is_datetime64_any_dtype(result)
        assert result.iloc[0].year == 2024

    def test_normalize_unix_microseconds(self):
        """Test normalization of Unix microseconds timestamps."""
        # Microseconds
        ts_series = pd.Series([1704067200000000, 1704067201000000])

        result = _normalize_timestamp(ts_series)

        assert pd.api.types.is_datetime64_any_dtype(result)
        assert result.iloc[0].year == 2024

    def test_normalize_unix_nanoseconds(self):
        """Test normalization of Unix nanoseconds timestamps."""
        # Nanoseconds
        ts_series = pd.Series([1704067200000000000, 1704067201000000000])

        result = _normalize_timestamp(ts_series)

        assert pd.api.types.is_datetime64_any_dtype(result)
        assert result.iloc[0].year == 2024

    def test_normalize_datetime_naive_to_utc(self):
        """Test that naive datetime is localized to UTC."""
        ts_series = pd.Series(
            [
                datetime(2024, 1, 1, 0, 0, 0),
                datetime(2024, 1, 1, 0, 0, 1),
            ]
        )

        result = _normalize_timestamp(ts_series)

        assert result.dt.tz == UTC

    def test_normalize_datetime_with_timezone(self):
        """Test that datetime with timezone is converted to UTC."""
        import zoneinfo

        est = zoneinfo.ZoneInfo("America/New_York")
        # 12:00 EST = 17:00 UTC
        ts_series = pd.Series(
            [
                datetime(2024, 1, 1, 12, 0, 0, tzinfo=est),
            ]
        )
        ts_series = pd.to_datetime(ts_series)

        result = _normalize_timestamp(ts_series)

        assert result.dt.tz == UTC
        # Should be converted to UTC (EST is UTC-5)
        assert result.iloc[0].hour == 17

    def test_normalize_already_utc_datetime(self):
        """Test that already-UTC datetime is passed through."""
        ts_series = pd.to_datetime(
            [
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:01Z",
            ],
            utc=True,
        )

        result = _normalize_timestamp(ts_series)

        # Should be datetime with UTC timezone
        assert result.dt.tz == UTC
        # Values should match
        assert (result == ts_series).all()

    def test_normalize_invalid_timestamp_raises_error(self):
        """Test that invalid timestamp format raises ValueError."""
        ts_series = pd.Series(["not a timestamp", "invalid"])

        with pytest.raises(ValueError, match="Failed to parse timestamps"):
            _normalize_timestamp(ts_series)


# ============================================================================
# Trade Normalization Tests
# ============================================================================


class TestNormalizeTrades:
    """Tests for normalize_trades function."""

    def test_normalize_valid_trades(self, sample_trades_df):
        """Test normalization of valid trade data."""
        result = normalize_trades(sample_trades_df, "test")

        assert len(result) == len(sample_trades_df)
        assert result["timestamp"].dt.tz == UTC
        assert all(result["price"] > 0)
        assert all(result["size"] > 0)
        assert all(result["aggressor_side"].isin(["BUY", "SELL"]))

    def test_normalize_empty_trades(self):
        """Test normalization of empty DataFrame."""
        df = pd.DataFrame(columns=["timestamp", "price", "size", "aggressor_side", "trade_id"])

        result = normalize_trades(df, "test")

        assert len(result) == 0

    def test_normalize_removes_negative_prices(self):
        """Test that trades with negative prices are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "price": [-50000.0, 50001.0],  # First is invalid
                "size": [0.1, 0.2],
                "aggressor_side": ["BUY", "SELL"],
                "trade_id": ["1", "2"],
            }
        )

        result = normalize_trades(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["price"] == 50001.0

    def test_normalize_removes_zero_prices(self):
        """Test that trades with zero prices are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "price": [0.0, 50001.0],  # First is invalid
                "size": [0.1, 0.2],
                "aggressor_side": ["BUY", "SELL"],
                "trade_id": ["1", "2"],
            }
        )

        result = normalize_trades(df, "test")

        assert len(result) == 1

    def test_normalize_removes_negative_sizes(self):
        """Test that trades with negative sizes are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "price": [50000.0, 50001.0],
                "size": [-0.1, 0.2],  # First is invalid
                "aggressor_side": ["BUY", "SELL"],
                "trade_id": ["1", "2"],
            }
        )

        result = normalize_trades(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["size"] == 0.2

    def test_normalize_removes_invalid_sides(self):
        """Test that trades with invalid aggressor_side are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "price": [50000.0, 50001.0],
                "size": [0.1, 0.2],
                "aggressor_side": ["INVALID", "SELL"],  # First is invalid
                "trade_id": ["1", "2"],
            }
        )

        result = normalize_trades(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["aggressor_side"] == "SELL"

    def test_normalize_uppercases_aggressor_side(self):
        """Test that aggressor_side is uppercased."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "price": [50000.0],
                "size": [0.1],
                "aggressor_side": ["buy"],  # Lowercase
                "trade_id": ["1"],
            }
        )

        result = normalize_trades(df, "test")

        assert result.iloc[0]["aggressor_side"] == "BUY"

    def test_normalize_sorts_by_timestamp(self, trades_df_unsorted):
        """Test that trades are sorted by timestamp."""
        result = normalize_trades(trades_df_unsorted, "test")

        # Should be sorted
        assert result["timestamp"].is_monotonic_increasing

    def test_normalize_removes_duplicates(self, trades_df_with_duplicates):
        """Test that duplicate trades are removed."""
        result = normalize_trades(trades_df_with_duplicates, "test")

        # Original has 3 rows with 2 duplicates (same timestamp + trade_id)
        # Should keep first occurrence
        assert len(result) < len(trades_df_with_duplicates)

    def test_normalize_converts_trade_id_to_string(self):
        """Test that trade_id is converted to string."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "price": [50000.0],
                "size": [0.1],
                "aggressor_side": ["BUY"],
                "trade_id": [12345],  # Integer
            }
        )

        result = normalize_trades(df, "test")

        assert isinstance(result.iloc[0]["trade_id"], str)
        assert result.iloc[0]["trade_id"] == "12345"

    def test_normalize_validates_schema(self, sample_trades_df):
        """Test that normalization validates against schema."""
        # This should not raise
        result = normalize_trades(sample_trades_df, "test")

        # Result should be valid
        assert all(result["price"] > 0)
        assert all(result["size"] > 0)


# ============================================================================
# Mark Price Normalization Tests
# ============================================================================


class TestNormalizeMarkPrices:
    """Tests for normalize_mark_prices function."""

    def test_normalize_valid_mark_prices(self, sample_mark_prices_df):
        """Test normalization of valid mark price data."""
        result = normalize_mark_prices(sample_mark_prices_df, "test")

        assert len(result) == len(sample_mark_prices_df)
        assert result["timestamp"].dt.tz == UTC
        assert all(result["mark_price"] > 0)

    def test_normalize_empty_mark_prices(self):
        """Test normalization of empty DataFrame."""
        df = pd.DataFrame(columns=["timestamp", "mark_price"])

        result = normalize_mark_prices(df, "test")

        assert len(result) == 0

    def test_normalize_removes_negative_mark_prices(self):
        """Test that negative mark prices are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "mark_price": [-50000.0, 50001.0],  # First is invalid
            }
        )

        result = normalize_mark_prices(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["mark_price"] == 50001.0

    def test_normalize_removes_zero_mark_prices(self):
        """Test that zero mark prices are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "mark_price": [0.0, 50001.0],  # First is invalid
            }
        )

        result = normalize_mark_prices(df, "test")

        assert len(result) == 1

    def test_normalize_sorts_mark_prices(self):
        """Test that mark prices are sorted by timestamp."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:02",
                        "2024-01-01 00:00:00",  # Out of order
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "mark_price": [50002.0, 50000.0, 50001.0],
            }
        )

        result = normalize_mark_prices(df, "test")

        assert result["timestamp"].is_monotonic_increasing
        assert result.iloc[0]["mark_price"] == 50000.0

    def test_normalize_removes_duplicate_mark_prices(self):
        """Test that duplicate timestamps are removed (keeps last)."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:00",  # Duplicate
                    ],
                    utc=True,
                ),
                "mark_price": [50000.0, 50001.0],
            }
        )

        result = normalize_mark_prices(df, "test")

        # Should keep last occurrence
        assert len(result) == 1
        assert result.iloc[0]["mark_price"] == 50001.0


# ============================================================================
# Funding Rate Normalization Tests
# ============================================================================


class TestNormalizeFundingRates:
    """Tests for normalize_funding_rates function."""

    def test_normalize_valid_funding_rates(self, sample_funding_rates_df):
        """Test normalization of valid funding rate data."""
        result = normalize_funding_rates(sample_funding_rates_df, "test")

        assert len(result) == len(sample_funding_rates_df)
        assert result["timestamp"].dt.tz == UTC
        assert "funding_rate" in result.columns

    def test_normalize_empty_funding_rates(self):
        """Test normalization of empty DataFrame."""
        df = pd.DataFrame(columns=["timestamp", "funding_rate"])

        result = normalize_funding_rates(df, "test")

        assert len(result) == 0

    def test_normalize_funding_rate_can_be_negative(self):
        """Test that negative funding rates are valid."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "funding_rate": [-0.0002],  # Negative is valid
            }
        )

        result = normalize_funding_rates(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["funding_rate"] == -0.0002

    def test_normalize_funding_rate_can_be_zero(self):
        """Test that zero funding rates are valid."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "funding_rate": [0.0],  # Zero is valid
            }
        )

        result = normalize_funding_rates(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["funding_rate"] == 0.0

    def test_normalize_removes_nan_funding_rates(self):
        """Test that NaN funding rates are removed."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "funding_rate": [np.nan, 0.0001],  # First is invalid
            }
        )

        result = normalize_funding_rates(df, "test")

        assert len(result) == 1
        assert result.iloc[0]["funding_rate"] == 0.0001

    def test_normalize_with_next_funding_time(self):
        """Test normalization with next_funding_time column."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "funding_rate": [0.0001],
                "next_funding_time": pd.to_datetime(["2024-01-01 08:00:00"], utc=True),
            }
        )

        result = normalize_funding_rates(df, "test")

        assert "next_funding_time" in result.columns
        assert result.iloc[0]["next_funding_time"].hour == 8

    def test_normalize_without_next_funding_time(self):
        """Test normalization without next_funding_time column."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "funding_rate": [0.0001],
            }
        )

        result = normalize_funding_rates(df, "test")

        assert "next_funding_time" in result.columns
        assert result.iloc[0]["next_funding_time"] is None

    def test_normalize_sorts_funding_rates(self):
        """Test that funding rates are sorted by timestamp."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 08:00:00",
                        "2024-01-01 00:00:00",  # Out of order
                    ],
                    utc=True,
                ),
                "funding_rate": [0.0002, 0.0001],
            }
        )

        result = normalize_funding_rates(df, "test")

        assert result["timestamp"].is_monotonic_increasing
        assert result.iloc[0]["funding_rate"] == 0.0001

    def test_normalize_removes_duplicate_funding_rates(self):
        """Test that duplicate timestamps are removed (keeps last)."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:00",  # Duplicate
                    ],
                    utc=True,
                ),
                "funding_rate": [0.0001, 0.0002],
            }
        )

        result = normalize_funding_rates(df, "test")

        assert len(result) == 1
        # Should keep last occurrence
        assert result.iloc[0]["funding_rate"] == 0.0002


# ============================================================================
# Performance and Edge Case Tests
# ============================================================================


class TestNormalizerPerformance:
    """Tests for normalizer performance and edge cases."""

    def test_normalize_large_dataset(self, large_trades_df):
        """Test normalization of large dataset (10k rows)."""
        result = normalize_trades(large_trades_df, "test")

        assert len(result) == len(large_trades_df)
        assert result["timestamp"].is_monotonic_increasing

    def test_normalize_all_invalid_data(self):
        """Test normalization when all data is invalid."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    [
                        "2024-01-01 00:00:00",
                        "2024-01-01 00:00:01",
                    ],
                    utc=True,
                ),
                "price": [-1.0, -2.0],  # All invalid
                "size": [0.1, 0.2],
                "aggressor_side": ["BUY", "SELL"],
                "trade_id": ["1", "2"],
            }
        )

        result = normalize_trades(df, "test")

        # Should return empty DataFrame with correct columns
        assert len(result) == 0
        assert "price" in result.columns

    def test_normalize_single_row(self):
        """Test normalization of single row."""
        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"], utc=True),
                "price": [50000.0],
                "size": [0.1],
                "aggressor_side": ["BUY"],
                "trade_id": ["1"],
            }
        )

        result = normalize_trades(df, "test")

        assert len(result) == 1
