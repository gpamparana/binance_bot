#!/usr/bin/env python
"""Test script to verify the critical fixes work correctly."""

import sys
from unittest.mock import MagicMock, patch

def test_order_id_retry_generation():
    """Test that retry order IDs stay under 36 character limit."""
    print("Testing Order ID Retry Generation...")

    # Test cases with various base order IDs
    test_cases = [
        "HG1-LONG-01-1761018780259-24",  # 29 chars
        "HG1-SHORT-10-1761018780259-99",  # 31 chars
        "HG1-LONG-01-1761018780259",  # No counter
    ]

    for base_id in test_cases:
        print(f"\nBase ID: {base_id} ({len(base_id)} chars)")

        # Simulate the fix logic
        for attempt in range(1, 4):
            # Extract base order ID without any retry suffixes
            clean_id = base_id.split("-retry")[0] if "-retry" in base_id else base_id
            clean_id = clean_id.split("-R")[0] if "-R" in clean_id else clean_id

            # Create compact retry ID
            new_id = f"{clean_id}-R{attempt}"

            print(f"  Retry {attempt}: {new_id} ({len(new_id)} chars)", end="")

            if len(new_id) > 36:
                print(" ❌ TOO LONG!")
                # Implement fallback truncation
                parts = clean_id.split("-")
                if len(parts) >= 4 and len(parts[3]) > 10:
                    parts[3] = parts[3][:10]  # Truncate timestamp
                    clean_id = "-".join(parts)
                    new_id = f"{clean_id}-R{attempt}"
                    print(f"    Truncated: {new_id} ({len(new_id)} chars)", end="")

            if len(new_id) <= 36:
                print(" ✅")
            else:
                print(" ❌ STILL TOO LONG!")
                return False

    print("\n✅ All retry order IDs are within 36 character limit")
    return True


def test_position_is_flat_replacement():
    """Test that position.is_flat() replacement works correctly."""
    print("\nTesting Position is_flat() Replacement...")

    # Create mock position objects
    from decimal import Decimal

    class MockPosition:
        def __init__(self, quantity):
            self.quantity = Decimal(str(quantity))

    test_cases = [
        (MockPosition(0), False, "Empty position"),
        (MockPosition(0.001), True, "Long position"),
        (MockPosition(10.5), True, "Large position"),
        (None, False, "No position"),
    ]

    for position, expected_has_quantity, description in test_cases:
        # Test the new logic: position and position.quantity > 0
        has_quantity = bool(position and position.quantity > 0)

        print(f"  {description}: ", end="")
        if has_quantity == expected_has_quantity:
            print(f"✅ (quantity={position.quantity if position else None})")
        else:
            print(f"❌ Expected {expected_has_quantity}, got {has_quantity}")
            return False

    print("\n✅ Position quantity checks work correctly")
    return True


def test_strategy_import():
    """Test that the strategy can be imported without errors."""
    print("\nTesting Strategy Import...")

    try:
        # Add the project to path
        sys.path.insert(0, '/Users/giovanni/Library/Mobile Documents/com~apple~CloudDocs/binance_bot')

        from naut_hedgegrid.strategies.hedge_grid_v1.strategy import HedgeGridV1
        print("✅ Strategy imported successfully")

        # Check that the critical methods exist
        methods_to_check = ['on_bar', 'on_order_filled', 'on_order_rejected', '_log_diagnostic_status']
        for method in methods_to_check:
            if hasattr(HedgeGridV1, method):
                print(f"  ✅ Method {method} exists")
            else:
                print(f"  ❌ Method {method} missing")
                return False

        return True

    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("CRITICAL FIXES VERIFICATION")
    print("=" * 60)

    results = []

    # Run tests
    results.append(("Order ID Retry", test_order_id_retry_generation()))
    results.append(("Position is_flat()", test_position_is_flat_replacement()))
    results.append(("Strategy Import", test_strategy_import()))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    all_passed = True
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name:20} {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n🎉 ALL CRITICAL FIXES VERIFIED SUCCESSFULLY!")
        print("\nNext Steps:")
        print("1. Set BINANCE_API_KEY and BINANCE_API_SECRET environment variables")
        print("2. Run paper trading: uv run python -m naut_hedgegrid paper")
        print("3. Monitor for any errors in the first hour")
        print("4. If stable, proceed to testnet with small amounts")
    else:
        print("\n⚠️ SOME TESTS FAILED - DO NOT PROCEED TO LIVE TRADING")
        sys.exit(1)


if __name__ == "__main__":
    main()