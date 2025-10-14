"""
Verification script for data pipeline implementation.

Run this to verify all components are working correctly.
"""

import sys
from datetime import UTC
from pathlib import Path


def check_imports():
    """Verify all modules can be imported."""
    print("Checking imports...")
    errors = []

    try:
        from naut_hedgegrid.data import pipelines, schemas, sources

        print("  ✓ Main data module")
    except ImportError as e:
        errors.append(f"  ✗ Main data module: {e}")

    try:
        from naut_hedgegrid.data.schemas import (
            FundingRateSchema,
            MarkPriceSchema,
            TradeSchema,
            to_trade_tick,
        )

        print("  ✓ Schemas module")
    except ImportError as e:
        errors.append(f"  ✗ Schemas module: {e}")

    try:
        from naut_hedgegrid.data.sources.base import DataSource
        from naut_hedgegrid.data.sources.csv_source import CSVDataSource
        from naut_hedgegrid.data.sources.websocket_source import WebSocketDataSource

        print("  ✓ Data sources")
    except ImportError as e:
        errors.append(f"  ✗ Data sources: {e}")

    try:
        from naut_hedgegrid.data.sources.tardis_source import TardisDataSource

        print("  ✓ Tardis source (tardis-client installed)")
    except ImportError as e:
        print(f"  ⚠ Tardis source: {e} (install with: pip install tardis-client)")

    try:
        from naut_hedgegrid.data.pipelines.normalizer import (
            normalize_funding_rates,
            normalize_mark_prices,
            normalize_trades,
        )

        print("  ✓ Normalizer")
    except ImportError as e:
        errors.append(f"  ✗ Normalizer: {e}")

    try:
        from naut_hedgegrid.data.pipelines.replay_to_parquet import run_pipeline

        print("  ✓ Pipeline orchestrator")
    except ImportError as e:
        errors.append(f"  ✗ Pipeline orchestrator: {e}")

    return errors


def check_file_structure():
    """Verify all files exist."""
    print("\nChecking file structure...")
    base = Path(__file__).parent.parent / "src" / "naut_hedgegrid" / "data"

    required_files = [
        "__init__.py",
        "schemas.py",
        "README.md",
        "sources/__init__.py",
        "sources/base.py",
        "sources/csv_source.py",
        "sources/tardis_source.py",
        "sources/websocket_source.py",
        "pipelines/__init__.py",
        "pipelines/normalizer.py",
        "pipelines/replay_to_parquet.py",
        "scripts/__init__.py",
        "scripts/generate_sample_data.py",
    ]

    missing = []
    for file in required_files:
        path = base / file
        if path.exists():
            print(f"  ✓ {file}")
        else:
            missing.append(file)
            print(f"  ✗ {file}")

    return missing


def check_dependencies():
    """Check required dependencies."""
    print("\nChecking dependencies...")
    errors = []

    try:
        import pandas

        print("  ✓ pandas")
    except ImportError:
        errors.append("  ✗ pandas")

    try:
        import pydantic

        print("  ✓ pydantic")
    except ImportError:
        errors.append("  ✗ pydantic")

    try:
        from nautilus_trader.model.data import TradeTick

        print("  ✓ nautilus-trader")
    except ImportError:
        errors.append("  ✗ nautilus-trader")

    try:
        import typer

        print("  ✓ typer")
    except ImportError:
        errors.append("  ✗ typer")

    try:
        from rich.console import Console

        print("  ✓ rich")
    except ImportError:
        errors.append("  ✗ rich")

    try:
        import aiohttp

        print("  ✓ aiohttp")
    except ImportError:
        print("  ⚠ aiohttp (install with: pip install aiohttp)")

    try:
        import tardis_client

        print("  ✓ tardis-client")
    except ImportError:
        print("  ⚠ tardis-client (optional, install with: pip install tardis-client)")

    return errors


def test_schema_validation():
    """Test schema validation."""
    print("\nTesting schema validation...")
    from datetime import datetime

    from naut_hedgegrid.data.schemas import FundingRateSchema, MarkPriceSchema, TradeSchema

    try:
        # Valid trade
        trade = TradeSchema(
            timestamp=datetime.now(UTC),
            price=100.0,
            size=1.0,
            aggressor_side="BUY",
            trade_id="123",
        )
        print("  ✓ TradeSchema validation")

        # Valid mark price
        mark = MarkPriceSchema(
            timestamp=datetime.now(UTC),
            mark_price=100.0,
        )
        print("  ✓ MarkPriceSchema validation")

        # Valid funding rate
        funding = FundingRateSchema(
            timestamp=datetime.now(UTC),
            funding_rate=0.0001,
        )
        print("  ✓ FundingRateSchema validation")

    except Exception as e:
        print(f"  ✗ Schema validation failed: {e}")
        return False

    return True


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("Data Pipeline Verification")
    print("=" * 60)

    all_passed = True

    # Check imports
    import_errors = check_imports()
    if import_errors:
        all_passed = False
        print("\nImport errors:")
        for error in import_errors:
            print(error)

    # Check file structure
    missing_files = check_file_structure()
    if missing_files:
        all_passed = False
        print("\nMissing files:")
        for file in missing_files:
            print(f"  - {file}")

    # Check dependencies
    dep_errors = check_dependencies()
    if dep_errors:
        all_passed = False
        print("\nMissing dependencies:")
        for error in dep_errors:
            print(error)

    # Test schema validation
    if not test_schema_validation():
        all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All checks passed!")
        print("\nNext steps:")
        print("  1. Set TARDIS_API_KEY environment variable")
        print("  2. Run: python -m naut_hedgegrid.data.scripts.generate_sample_data")
        print("  3. Run backtest with generated catalog")
    else:
        print("✗ Some checks failed. Please review errors above.")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
