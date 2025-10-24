"""CLI interface for strategy optimization."""

from pathlib import Path

import typer
from rich.console import Console

from naut_hedgegrid.optimization import StrategyOptimizer

app = typer.Typer(help="HedgeGridV1 Parameter Optimization")
console = Console()


@app.command()
def optimize(
    backtest_config: Path = typer.Option(
        ...,
        "--backtest-config",
        "-b",
        help="Path to backtest configuration YAML",
        exists=True,
        dir_okay=False
    ),
    strategy_config: Path = typer.Option(
        ...,
        "--strategy-config",
        "-s",
        help="Path to base strategy configuration YAML",
        exists=True,
        dir_okay=False
    ),
    n_trials: int = typer.Option(
        100,
        "--trials",
        "-n",
        help="Number of optimization trials to run",
        min=1,
        max=10000
    ),
    study_name: str | None = typer.Option(
        None,
        "--study-name",
        help="Name for optimization study (default: auto-generated)"
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="Path to results database (default: optimization_results.db)"
    ),
    export_csv: Path | None = typer.Option(
        None,
        "--export-csv",
        help="Export results to CSV file"
    ),
    storage: str | None = typer.Option(
        None,
        "--storage",
        help="Optuna storage URL (e.g., sqlite:///optuna.db)"
    ),
    no_verbose: bool = typer.Option(
        False,
        "--no-verbose",
        help="Disable verbose output"
    ),
):
    """
    Run parameter optimization for HedgeGridV1 strategy.

    This command performs Bayesian optimization using Optuna to find
    optimal strategy parameters. Results are saved to a SQLite database
    and the best parameters are exported to a YAML config file.

    Example:

        uv run python -m naut_hedgegrid.optimization.cli optimize \\
            --backtest-config configs/backtest/btcusdt_mark_trades_funding.yaml \\
            --strategy-config configs/strategies/hedge_grid_v1.yaml \\
            --trials 200 \\
            --study-name btcusdt_opt_v1
    """
    console.print("\n[bold cyan]HedgeGridV1 Parameter Optimization[/bold cyan]\n")

    # Initialize optimizer
    optimizer = StrategyOptimizer(
        backtest_config_path=backtest_config,
        base_strategy_config_path=strategy_config,
        n_trials=n_trials,
        n_jobs=1,  # Sequential execution
        study_name=study_name,
        db_path=db_path,
        storage=storage,
        verbose=not no_verbose
    )

    # Run optimization
    try:
        study = optimizer.optimize()

        # Export results if requested
        if export_csv:
            optimizer.export_results(export_csv)

        console.print("\n[green]✓ Optimization complete![/green]")
        console.print(f"Best score: {study.best_value:.4f}")
        console.print(f"Best trial: {study.best_trial.number}")

    except KeyboardInterrupt:
        console.print("\n[yellow]Optimization interrupted by user[/yellow]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def analyze(
    study_name: str = typer.Argument(..., help="Name of optimization study"),
    db_path: Path = typer.Option(
        Path("optimization_results.db"),
        "--db-path",
        help="Path to results database"
    ),
    top_n: int = typer.Option(
        10,
        "--top-n",
        "-n",
        help="Number of top trials to show",
        min=1,
        max=100
    ),
    export_csv: Path | None = typer.Option(
        None,
        "--export-csv",
        help="Export results to CSV file"
    ),
):
    """
    Analyze optimization results.

    Shows statistics and top performing trials from a completed
    optimization study.

    Example:

        uv run python -m naut_hedgegrid.optimization.cli analyze btcusdt_opt_v1 \\
            --top-n 5 \\
            --export-csv results.csv
    """
    from rich.table import Table

    from naut_hedgegrid.optimization import OptimizationResultsDB

    if not db_path.exists():
        console.print(f"[red]Error: Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    # Connect to database
    db = OptimizationResultsDB(db_path=db_path)

    # Get study statistics
    stats = db.get_study_stats(study_name)

    if "error" in stats:
        console.print(f"[red]Error: {stats['error']}[/red]")
        raise typer.Exit(1)

    # Display statistics
    console.print(f"\n[bold]Study: {study_name}[/bold]\n")

    stats_table = Table(show_header=False, box=None)
    stats_table.add_row("Total Trials", str(stats["total_trials"]))
    stats_table.add_row("Valid Trials", str(stats["valid_trials"]))
    stats_table.add_row("Validity Rate", f"{stats['validity_rate']:.1%}")
    stats_table.add_row("Best Score", f"{stats['best_score']:.4f}")
    stats_table.add_row("Avg Score", f"{stats['avg_score']:.4f}")
    stats_table.add_row("Avg Sharpe", f"{stats['avg_sharpe']:.2f}")
    stats_table.add_row("Avg Drawdown", f"{stats['avg_drawdown_pct']:.1f}%")

    console.print(stats_table)

    # Get top trials
    console.print(f"\n[bold]Top {top_n} Trials[/bold]\n")

    best_trials = db.get_best_trials(study_name, n=top_n)

    trials_table = Table()
    trials_table.add_column("Trial", style="cyan")
    trials_table.add_column("Score", style="green")
    trials_table.add_column("Sharpe", style="yellow")
    trials_table.add_column("Profit Factor", style="yellow")
    trials_table.add_column("Max DD%", style="red")
    trials_table.add_column("Trades", style="blue")

    for trial in best_trials:
        metrics = trial["metrics"]
        trials_table.add_row(
            str(trial["id"]),
            f"{trial['score']:.4f}",
            f"{metrics.get('sharpe_ratio', 0):.2f}",
            f"{metrics.get('profit_factor', 0):.2f}",
            f"{metrics.get('max_drawdown_pct', 0):.1f}",
            str(metrics.get("total_trades", 0))
        )

    console.print(trials_table)

    # Export if requested
    if export_csv:
        db.export_to_csv(study_name, export_csv)
        console.print(f"\n[green]✓ Results exported to {export_csv}[/green]")


@app.command()
def cleanup(
    study_name: str = typer.Argument(..., help="Name of optimization study"),
    db_path: Path = typer.Option(
        Path("optimization_results.db"),
        "--db-path",
        help="Path to results database"
    ),
    keep_top_n: int = typer.Option(
        100,
        "--keep-top-n",
        help="Number of top trials to keep",
        min=1,
        max=1000
    ),
):
    """
    Cleanup old trials from database.

    Removes low-performing trials to reduce database size,
    keeping only the top N best trials.

    Example:

        uv run python -m naut_hedgegrid.optimization.cli cleanup btcusdt_opt_v1 \\
            --keep-top-n 50
    """
    from naut_hedgegrid.optimization import OptimizationResultsDB

    if not db_path.exists():
        console.print(f"[red]Error: Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    # Connect to database
    db = OptimizationResultsDB(db_path=db_path)

    # Get current count
    stats = db.get_study_stats(study_name)
    if "error" in stats:
        console.print(f"[red]Error: {stats['error']}[/red]")
        raise typer.Exit(1)

    current_count = stats["total_trials"]

    # Cleanup
    db.cleanup_old_trials(study_name, keep_top_n=keep_top_n)

    # Get new count
    stats = db.get_study_stats(study_name)
    new_count = stats["total_trials"]

    removed = current_count - new_count

    console.print("[green]✓ Cleanup complete[/green]")
    console.print(f"  Removed: {removed} trials")
    console.print(f"  Remaining: {new_count} trials")


if __name__ == "__main__":
    app()
