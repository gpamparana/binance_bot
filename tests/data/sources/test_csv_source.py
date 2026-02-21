"""
Tests for CSV data source implementation.

Tests file reading, column mapping, compression support, and data filtering
for the CSVDataSource class.
"""

import gzip
from datetime import UTC, datetime

import pytest

from naut_hedgegrid.data.sources.csv_source import CSVDataSource

# ============================================================================
# File Reading Tests
# ============================================================================


class TestCSVFileReading:
    """Tests for CSV file reading functionality."""

    def test_read_uncompressed_csv(self, tmp_path):
        """Test reading uncompressed CSV file."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,price,size,side,id\n"
            "2024-01-01T00:00:00Z,50000,0.1,BUY,123\n"
            "2024-01-01T00:00:01Z,50001,0.2,SELL,124\n"
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = source._read_csv(csv_file)

        assert len(df) == 2
        assert "price" in df.columns

    def test_read_gzipped_csv(self, tmp_path):
        """Test reading gzip compressed CSV file."""
        csv_file = tmp_path / "trades.csv.gz"

        # Write compressed CSV
        csv_data = "timestamp,price,size,side,id\n2024-01-01T00:00:00Z,50000,0.1,BUY,123\n"
        with gzip.open(csv_file, "wt") as f:
            f.write(csv_data)

        config = {"trades": {"file_path": "trades.csv.gz"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = source._read_csv(csv_file)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000

    def test_file_not_found_raises_error(self, tmp_path):
        """Test that missing file raises FileNotFoundError."""
        config = {"trades": {"file_path": "nonexistent.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(FileNotFoundError):
            source._read_csv(tmp_path / "nonexistent.csv")

    def test_invalid_csv_format(self, tmp_path):
        """Test handling of invalid CSV format."""
        csv_file = tmp_path / "invalid.csv"
        csv_file.write_text("not,valid,csv\ndata")

        config = {"trades": {"file_path": "invalid.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        # Should read but may not have expected columns
        df = source._read_csv(csv_file)
        assert len(df) == 1


# ============================================================================
# Column Mapping Tests
# ============================================================================


class TestCSVColumnMapping:
    """Tests for column mapping functionality."""

    def test_explicit_column_mapping(self, tmp_path):
        """Test explicit column mapping works."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text("time,last_price,volume,taker_side,tx_id\n2024-01-01T00:00:00Z,50000,0.1,BUY,123\n")

        config = {
            "trades": {
                "file_path": "trades.csv",
                "columns": {
                    "timestamp": "time",
                    "price": "last_price",
                    "size": "volume",
                    "aggressor_side": "taker_side",
                    "trade_id": "tx_id",
                },
            }
        }
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = source._read_csv(csv_file)
        df = source._map_columns(
            df,
            config["trades"].get("columns", {}),
            source.DEFAULT_TRADE_COLUMNS,
        )

        assert "timestamp" in df.columns
        assert "price" in df.columns
        assert "size" in df.columns

    def test_auto_detection_common_names(self, tmp_path):
        """Test auto-detection of common column names."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text("time,price,quantity,side,id\n2024-01-01T00:00:00Z,50000,0.1,BUY,123\n")

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = source._read_csv(csv_file)
        df = source._map_columns(
            df,
            {},
            source.DEFAULT_TRADE_COLUMNS,
        )

        # Auto-detection should map these
        assert "timestamp" in df.columns  # time -> timestamp
        assert "size" in df.columns  # quantity -> size
        assert "trade_id" in df.columns  # id -> trade_id

    def test_case_insensitive_column_matching(self, tmp_path):
        """Test that column matching is case-insensitive."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text("TIMESTAMP,PRICE,SIZE,SIDE,ID\n2024-01-01T00:00:00Z,50000,0.1,BUY,123\n")

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = source._read_csv(csv_file)
        df = source._map_columns(
            df,
            {},
            source.DEFAULT_TRADE_COLUMNS,
        )

        # Uppercase columns should be detected
        assert "timestamp" in df.columns


# ============================================================================
# Data Fetching Tests
# ============================================================================


@pytest.mark.asyncio
class TestCSVDataFetching:
    """Tests for data fetching methods."""

    async def test_fetch_trades_success(self, tmp_path, start_date, end_date):
        """Test successful trade data fetching."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,price,size,aggressor_side,trade_id\n"
            "2024-01-01T00:00:00Z,50000,0.1,BUY,123\n"
            "2024-01-01T12:00:00Z,50001,0.2,SELL,124\n"
            "2024-01-02T00:00:00Z,50002,0.3,BUY,125\n"
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 3
        assert all(col in df.columns for col in ["timestamp", "price", "size", "aggressor_side", "trade_id"])

    async def test_fetch_trades_date_range_filter(self, tmp_path):
        """Test that date range filtering works."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,price,size,aggressor_side,trade_id\n"
            "2024-01-01T00:00:00Z,50000,0.1,BUY,123\n"
            "2024-01-02T00:00:00Z,50001,0.2,SELL,124\n"
            "2024-01-03T00:00:00Z,50002,0.3,BUY,125\n"
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        # Fetch only Jan 2
        start = datetime(2024, 1, 2, tzinfo=UTC)
        end = datetime(2024, 1, 3, tzinfo=UTC)

        df = await source.fetch_trades("BTCUSDT", start, end)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50001

    async def test_fetch_trades_symbol_filter(self, tmp_path, start_date, end_date):
        """Test that symbol filtering works when CSV has symbol column."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,symbol,price,size,aggressor_side,trade_id\n"
            "2024-01-01T00:00:00Z,BTCUSDT,50000,0.1,BUY,123\n"
            "2024-01-01T00:00:01Z,ETHUSDT,3000,0.5,SELL,124\n"
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000

    async def test_fetch_trades_missing_config(self, tmp_path, start_date, end_date):
        """Test that missing trades config raises error."""
        config = {}  # No trades config
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(ValueError, match="No trades configuration"):
            await source.fetch_trades("BTCUSDT", start_date, end_date)

    async def test_fetch_trades_missing_columns(self, tmp_path, start_date, end_date):
        """Test that missing required columns raises error."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,price\n"  # Missing size, aggressor_side, trade_id
            "2024-01-01T00:00:00Z,50000\n"
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(ValueError, match="Missing required trade columns"):
            await source.fetch_trades("BTCUSDT", start_date, end_date)

    async def test_fetch_mark_prices_success(self, tmp_path, start_date, end_date):
        """Test successful mark price fetching."""
        csv_file = tmp_path / "mark.csv"
        csv_file.write_text("timestamp,mark_price\n2024-01-01T00:00:00Z,50001\n2024-01-01T12:00:00Z,50002\n")

        config = {"mark": {"file_path": "mark.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_mark_prices("BTCUSDT", start_date, end_date)

        assert len(df) == 2
        assert all(col in df.columns for col in ["timestamp", "mark_price"])

    async def test_fetch_funding_rates_success(self, tmp_path, start_date, end_date):
        """Test successful funding rate fetching."""
        csv_file = tmp_path / "funding.csv"
        csv_file.write_text(
            "timestamp,funding_rate,next_funding_time\n2024-01-01T00:00:00Z,0.0001,2024-01-01T08:00:00Z\n"
        )

        config = {"funding": {"file_path": "funding.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_funding_rates("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert "funding_rate" in df.columns
        assert "next_funding_time" in df.columns

    async def test_fetch_funding_without_next_funding_time(self, tmp_path, start_date, end_date):
        """Test funding rate fetching when next_funding_time is missing."""
        csv_file = tmp_path / "funding.csv"
        csv_file.write_text("timestamp,funding_rate\n2024-01-01T00:00:00Z,0.0001\n")

        config = {"funding": {"file_path": "funding.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_funding_rates("BTCUSDT", start_date, end_date)

        assert "next_funding_time" in df.columns
        assert df.iloc[0]["next_funding_time"] is None


# ============================================================================
# Edge Cases and Performance Tests
# ============================================================================


@pytest.mark.asyncio
class TestCSVEdgeCases:
    """Tests for edge cases and special scenarios."""

    async def test_empty_csv_file(self, tmp_path, start_date, end_date):
        """Test handling of empty CSV file."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text("timestamp,price,size,aggressor_side,trade_id\n")

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 0

    async def test_csv_with_special_characters(self, tmp_path, start_date, end_date):
        """Test CSV with special characters in trade_id."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            'timestamp,price,size,aggressor_side,trade_id\n2024-01-01T00:00:00Z,50000,0.1,BUY,"abc-123_xyz"\n'
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["trade_id"] == "abc-123_xyz"

    async def test_different_date_formats(self, tmp_path, start_date, end_date):
        """Test handling of different timestamp formats."""
        csv_file = tmp_path / "trades.csv"
        csv_file.write_text(
            "timestamp,price,size,aggressor_side,trade_id\n2024-01-01 00:00:00,50000,0.1,BUY,123\n"  # Different format
        )

        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        # Should parse and convert to UTC
        assert df.iloc[0]["timestamp"].tzinfo == UTC

    def test_repr(self, tmp_path):
        """Test string representation of CSVDataSource."""
        config = {"trades": {"file_path": "trades.csv"}}
        source = CSVDataSource(config=config, base_path=str(tmp_path))

        repr_str = repr(source)

        assert "CSVDataSource" in repr_str
        assert str(tmp_path) in repr_str
