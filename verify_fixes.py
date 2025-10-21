#!/usr/bin/env python
"""Verify both fixes are properly implemented without needing API access."""

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def verify_grid_center_fix():
    """Verify the grid center fix is properly implemented."""
    strategy_file = Path("naut_hedgegrid/strategies/hedge_grid_v1/strategy.py")

    with open(strategy_file, "r") as f:
        lines = f.readlines()

    # Find the build_ladders call
    for i, line in enumerate(lines):
        if "ladders = GridEngine.build_ladders(" in line:
            # Check next few lines for the fix
            context = "".join(lines[i:i+5])
            if "mid=self._grid_center" in context:
                return True, f"Line {i+2}: Using self._grid_center (CORRECT)"
            elif "mid=mid" in context:
                return False, f"Line {i+2}: Using mid (INCORRECT - will reset every bar)"

    return False, "build_ladders call not found"


def verify_warmup_implementation():
    """Verify warmup is implemented in strategy and runner."""
    checks = []

    # Check strategy has warmup method
    strategy_file = Path("naut_hedgegrid/strategies/hedge_grid_v1/strategy.py")
    with open(strategy_file, "r") as f:
        content = f.read()

    if "def warmup_regime_detector(" in content:
        checks.append(("Strategy warmup method", True, "✓ Found warmup_regime_detector()"))
    else:
        checks.append(("Strategy warmup method", False, "✗ Method not found"))

    # Check runner has warmup integration
    runner_file = Path("naut_hedgegrid/runners/base_runner.py")
    with open(runner_file, "r") as f:
        content = f.read()

    if "def _warmup_strategy(" in content:
        checks.append(("Runner warmup method", True, "✓ Found _warmup_strategy()"))
    else:
        checks.append(("Runner warmup method", False, "✗ Method not found"))

    if "self._warmup_strategy(" in content:
        checks.append(("Runner integration", True, "✓ Warmup called in run()"))
    else:
        checks.append(("Runner integration", False, "✗ Not integrated"))

    # Check warmup module exists
    warmup_module = Path("naut_hedgegrid/warmup/binance_warmer.py")
    if warmup_module.exists():
        checks.append(("Warmup module", True, "✓ BinanceDataWarmer exists"))
    else:
        checks.append(("Warmup module", False, "✗ Module not found"))

    return checks


def main():
    """Verify all fixes are in place."""
    console.print("\n[bold cyan]Verifying Grid Reset Fix and Warmup Implementation[/bold cyan]\n")

    # Create results table
    table = Table(title="Fix Verification Results", show_header=True)
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Details")

    # Fix 1: Grid Center
    console.print("[bold]1. Grid Center Fix (Prevents resetting every minute)[/bold]")
    grid_ok, grid_msg = verify_grid_center_fix()
    if grid_ok:
        console.print(f"  [green]✓[/green] {grid_msg}")
        table.add_row("Grid Center Fix", "[green]PASS[/green]", grid_msg)
    else:
        console.print(f"  [red]✗[/red] {grid_msg}")
        table.add_row("Grid Center Fix", "[red]FAIL[/red]", grid_msg)

    console.print()

    # Fix 2: Warmup System
    console.print("[bold]2. Warmup Implementation (Speeds up trading start)[/bold]")
    warmup_checks = verify_warmup_implementation()
    warmup_ok = all(check[1] for check in warmup_checks)

    for name, passed, msg in warmup_checks:
        if passed:
            console.print(f"  [green]{msg}[/green]")
        else:
            console.print(f"  [red]{msg}[/red]")
        table.add_row(f"  {name}", "[green]PASS[/green]" if passed else "[red]FAIL[/red]", msg)

    console.print()
    console.print(table)
    console.print()

    # Overall status
    all_ok = grid_ok and warmup_ok

    if all_ok:
        panel = Panel(
            "[bold green]✅ All fixes are properly implemented![/bold green]\n\n"
            "The grid will now:\n"
            "• Use stable center prices (no more resetting every minute)\n"
            "• Warm up indicators with historical data at startup\n\n"
            "[cyan]Ready to test on testnet with:[/cyan]\n"
            "python -m naut_hedgegrid paper --venue-config configs/venues/binance_futures_testnet.yaml",
            title="[green]Success[/green]",
            border_style="green"
        )
        console.print(panel)
    else:
        panel = Panel(
            "[bold red]⚠ Some fixes are missing or incorrect![/bold red]\n\n"
            "Please review the issues above and ensure:\n"
            "• Grid uses self._grid_center, not mid\n"
            "• Warmup methods are implemented\n"
            "• Warmup module is installed",
            title="[red]Issues Found[/red]",
            border_style="red"
        )
        console.print(panel)

    # Expected behavior
    console.print("\n[bold]Expected Behavior After Fixes:[/bold]")
    console.print("1. [cyan]Grid Stability:[/cyan]")
    console.print("   - Orders stay at fixed prices (e.g., 110000, 110275, 110550)")
    console.print("   - Logs show: 'Diff result: 0 adds, 0 cancels, 0 replaces' (most bars)")
    console.print("   - Recenter only when price moves >150 bps from center")
    console.print()
    console.print("2. [cyan]Warmup at Startup:[/cyan]")
    console.print("   - Fetches 70 historical bars before trading")
    console.print("   - Regime detector starts warm (EMA/ADX ready)")
    console.print("   - Trading decisions informed from first bar")
    console.print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())