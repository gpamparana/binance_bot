"""
Data ingestion and pipeline module for NautilusTrader backtests.

This module provides:
- Schemas for market data validation
- Data sources (Tardis.dev, CSV, WebSocket)
- Normalization pipelines
- Conversion to Nautilus types
- ParquetDataCatalog integration
"""

from naut_hedgegrid.data import pipelines, schemas, sources

__all__ = ["pipelines", "schemas", "sources"]
