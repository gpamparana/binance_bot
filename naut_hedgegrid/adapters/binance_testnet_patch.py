"""
Monkey patch for Nautilus Binance adapter to handle testnet non-ASCII symbols.

Binance testnet includes test instruments with Chinese characters (测试测试USDT-PERP)
which cause Nautilus to crash when parsing symbols. This patch filters out non-ASCII
symbols before they reach Nautilus's Rust code.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nautilus_trader.adapters.binance.futures.http.account import (
        BinanceFuturesAccountHttpAPI,
    )


def patch_binance_futures_for_testnet():
    """Patch BinanceFuturesAccountHttpAPI to filter non-ASCII symbols."""
    from nautilus_trader.adapters.binance.futures.http.account import (
        BinanceFuturesAccountHttpAPI,
    )

    # Save original method
    original_query = BinanceFuturesAccountHttpAPI.query_futures_position_risk

    async def patched_query_futures_position_risk(self, recv_window: str | None = None):
        """Query position risk but filter out non-ASCII symbols."""
        # Call original method
        position_risks = await original_query(self, recv_window=recv_window)

        # Filter out positions with non-ASCII symbols
        filtered_risks = []
        for pos in position_risks:
            symbol = pos.symbol
            try:
                # Check if symbol is ASCII
                symbol.encode("ascii")
                filtered_risks.append(pos)
            except UnicodeEncodeError:
                # Skip non-ASCII symbols (Chinese test instruments)
                pass

        return filtered_risks

    # Replace method
    BinanceFuturesAccountHttpAPI.query_futures_position_risk = (
        patched_query_futures_position_risk
    )

    return True
