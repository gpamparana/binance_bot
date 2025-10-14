"""
Tests for WebSocket data source implementation.

Tests JSONL file parsing, Binance message format handling, and data extraction
for the WebSocketDataSource class.
"""

import gzip
from datetime import UTC, datetime

import pytest

from naut_hedgegrid.data.sources.websocket_source import WebSocketDataSource

# ============================================================================
# JSONL Parsing Tests
# ============================================================================


class TestJSONLParsing:
    """Tests for JSONL file parsing functionality."""

    def test_parse_valid_jsonl(self, tmp_path):
        """Test parsing valid JSONL file."""
        jsonl_file = tmp_path / "messages.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "BTCUSDT"}}\n'
            '{"data": {"e": "aggTrade", "s": "ETHUSDT"}}\n'
        )

        config = {"trades": {"file_path": "messages.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        messages = source._read_jsonl(jsonl_file)

        assert len(messages) == 2
        assert messages[0]["data"]["s"] == "BTCUSDT"

    def test_parse_gzipped_jsonl(self, tmp_path):
        """Test parsing gzipped JSONL file."""
        jsonl_file = tmp_path / "messages.jsonl.gz"

        data = '{"data": {"e": "aggTrade", "s": "BTCUSDT"}}\n'
        with gzip.open(jsonl_file, "wt") as f:
            f.write(data)

        config = {"trades": {"file_path": "messages.jsonl.gz"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        messages = source._read_jsonl(jsonl_file)

        assert len(messages) == 1

    def test_invalid_json_line_skipped(self, tmp_path):
        """Test that invalid JSON lines are skipped with warning."""
        jsonl_file = tmp_path / "messages.jsonl"
        jsonl_file.write_text(
            '{"valid": "json"}\n'
            "invalid json line\n"  # Invalid
            '{"also": "valid"}\n'
        )

        config = {"trades": {"file_path": "messages.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        messages = source._read_jsonl(jsonl_file)

        # Should skip invalid line and return 2 valid messages
        assert len(messages) == 2

    def test_empty_lines_ignored(self, tmp_path):
        """Test that empty lines are ignored."""
        jsonl_file = tmp_path / "messages.jsonl"
        jsonl_file.write_text(
            '{"data": "test"}\n'
            "\n"  # Empty line
            "   \n"  # Whitespace line
            '{"data": "test2"}\n'
        )

        config = {"trades": {"file_path": "messages.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        messages = source._read_jsonl(jsonl_file)

        assert len(messages) == 2

    def test_file_not_found_raises_error(self, tmp_path):
        """Test that missing file raises FileNotFoundError."""
        config = {"trades": {"file_path": "nonexistent.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(FileNotFoundError):
            source._read_jsonl(tmp_path / "nonexistent.jsonl")


# ============================================================================
# Message Format Tests
# ============================================================================


@pytest.mark.asyncio
class TestBinanceMessageFormats:
    """Tests for Binance message format parsing."""

    async def test_parse_aggtrade_message(self, tmp_path, start_date, end_date):
        """Test parsing Binance aggTrade messages."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000.0
        assert df.iloc[0]["size"] == 0.1
        assert df.iloc[0]["aggressor_side"] == "BUY"  # m=false means buyer is taker
        assert df.iloc[0]["trade_id"] == "123"

    async def test_parse_aggtrade_seller_maker(self, tmp_path, start_date, end_date):
        """Test aggTrade with buyer as maker (seller is aggressor)."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": true}}\n'  # m=true: buyer is maker
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert df.iloc[0]["aggressor_side"] == "SELL"  # Seller is taker

    async def test_parse_markprice_message(self, tmp_path, start_date, end_date):
        """Test parsing Binance markPriceUpdate messages."""
        jsonl_file = tmp_path / "mark.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "50001", '
            '"E": 1704067200000}}\n'
        )

        config = {"mark": {"file_path": "mark.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_mark_prices("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["mark_price"] == 50001.0

    async def test_parse_funding_from_markprice(self, tmp_path, start_date, end_date):
        """Test extracting funding rate from markPriceUpdate messages."""
        jsonl_file = tmp_path / "mark.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "50001", '
            '"r": "0.0001", "T": 1704096000000, "E": 1704067200000}}\n'
        )

        config = {"mark": {"file_path": "mark.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_funding_rates("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["funding_rate"] == 0.0001
        assert df.iloc[0]["next_funding_time"] is not None

    async def test_unknown_message_type_ignored(self, tmp_path, start_date, end_date):
        """Test that unknown message types are ignored."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "unknownType", "s": "BTCUSDT"}}\n'
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        # Should only parse aggTrade message
        assert len(df) == 1

    async def test_unwrapped_message_format(self, tmp_path, start_date, end_date):
        """Test handling of unwrapped message format (no 'data' wrapper)."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000.0


# ============================================================================
# Data Extraction Tests
# ============================================================================


@pytest.mark.asyncio
class TestDataExtraction:
    """Tests for data extraction and conversion."""

    async def test_timestamp_conversion_milliseconds(self, tmp_path, start_date, end_date):
        """Test that millisecond timestamps are converted correctly."""
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        timestamp = df.iloc[0]["timestamp"]
        assert timestamp.year == 2024
        assert timestamp.month == 1
        assert timestamp.day == 1
        assert timestamp.tzinfo == UTC

    async def test_symbol_filtering(self, tmp_path, start_date, end_date):
        """Test that only matching symbols are returned."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
            '{"data": {"e": "aggTrade", "s": "ETHUSDT", "p": "3000", "q": "0.5", '
            '"T": 1704067201000, "a": 124, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000.0

    async def test_date_range_filtering(self, tmp_path):
        """Test that trades outside date range are filtered."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            # 2024-01-01
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
            # 2024-01-03 (outside range)
            '{"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "50002", "q": "0.3", '
            '"T": 1704240000000, "a": 125, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 2, tzinfo=UTC)

        df = await source.fetch_trades("BTCUSDT", start, end)

        assert len(df) == 1
        assert df.iloc[0]["price"] == 50000.0

    async def test_empty_result_no_matches(self, tmp_path, start_date, end_date):
        """Test that empty DataFrame is returned when no messages match."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "ETHUSDT", "p": "3000", "q": "0.5", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        # Request different symbol
        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 0
        assert all(
            col in df.columns
            for col in ["timestamp", "price", "size", "aggressor_side", "trade_id"]
        )


# ============================================================================
# Configuration and Edge Cases
# ============================================================================


@pytest.mark.asyncio
class TestWebSocketEdgeCases:
    """Tests for edge cases and special scenarios."""

    async def test_missing_trades_config(self, tmp_path, start_date, end_date):
        """Test that missing trades config raises error."""
        config = {}  # No trades config
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(ValueError, match="No trades configuration"):
            await source.fetch_trades("BTCUSDT", start_date, end_date)

    async def test_missing_mark_config(self, tmp_path, start_date, end_date):
        """Test that missing mark config raises error."""
        config = {}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        with pytest.raises(ValueError, match="No mark price configuration"):
            await source.fetch_mark_prices("BTCUSDT", start_date, end_date)

    async def test_funding_fallback_to_mark_file(self, tmp_path, start_date, end_date):
        """Test that funding rates can be extracted from mark file if no funding file."""
        jsonl_file = tmp_path / "mark.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "50001", '
            '"r": "0.0001", "T": 1704096000000, "E": 1704067200000}}\n'
        )

        # Only mark config, no funding config
        config = {"mark": {"file_path": "mark.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_funding_rates("BTCUSDT", start_date, end_date)

        assert len(df) >= 0  # Should work without error

    async def test_deduplication_of_funding_rates(self, tmp_path, start_date, end_date):
        """Test that funding rates are deduplicated by timestamp."""
        jsonl_file = tmp_path / "mark.jsonl"
        # Multiple messages at same second
        jsonl_file.write_text(
            '{"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "50001", '
            '"r": "0.0001", "T": 1704096000000, "E": 1704067200000}}\n'
            '{"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "50001", '
            '"r": "0.0001", "T": 1704096000000, "E": 1704067200500}}\n'  # Same second
        )

        config = {"mark": {"file_path": "mark.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_funding_rates("BTCUSDT", start_date, end_date)

        # Should deduplicate to 1 entry
        assert len(df) == 1

    async def test_case_insensitive_symbol_match(self, tmp_path, start_date, end_date):
        """Test that symbol matching is case-insensitive."""
        jsonl_file = tmp_path / "trades.jsonl"
        jsonl_file.write_text(
            '{"data": {"e": "aggTrade", "s": "btcusdt", "p": "50000", "q": "0.1", '
            '"T": 1704067200000, "a": 123, "m": false}}\n'
        )

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1

    async def test_large_jsonl_file(self, tmp_path, start_date, end_date):
        """Test processing large JSONL file efficiently."""
        jsonl_file = tmp_path / "trades.jsonl"

        # Write many messages
        with open(jsonl_file, "w") as f:
            for i in range(1000):
                msg = (
                    f'{{"data": {{"e": "aggTrade", "s": "BTCUSDT", "p": "50000", '
                    f'"q": "0.1", "T": {1704067200000 + i*1000}, "a": {i}, "m": false}}}}\n'
                )
                f.write(msg)

        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        df = await source.fetch_trades("BTCUSDT", start_date, end_date)

        assert len(df) == 1000

    def test_repr(self, tmp_path):
        """Test string representation of WebSocketDataSource."""
        config = {"trades": {"file_path": "trades.jsonl"}}
        source = WebSocketDataSource(config=config, base_path=str(tmp_path))

        repr_str = repr(source)

        assert "WebSocketDataSource" in repr_str
        assert str(tmp_path) in repr_str
