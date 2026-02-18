"""Parallel backtest execution with multiprocessing.

This module provides concurrent backtest execution using ProcessPoolExecutor
for efficient parameter optimization across multiple CPU cores.
"""

import logging
import multiprocessing as mp
import tempfile
import traceback
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from naut_hedgegrid.metrics.report import PerformanceMetrics, ReportGenerator
from naut_hedgegrid.runners.run_backtest import BacktestRunner


# Module-level function for pickling with multiprocessing
def _run_single_backtest(
    args: tuple[Path, Path, dict[str, Any], int],
) -> tuple[PerformanceMetrics | None, str | None]:
    """
    Run a single backtest in a separate process.

    This function is defined at module level to be picklable for multiprocessing.

    Parameters
    ----------
    args : Tuple
        Tuple of (backtest_config_path, strategy_config_path, parameters, trial_id)

    Returns
    -------
    Tuple[Optional[PerformanceMetrics], Optional[str]]
        Performance metrics if successful, error message if failed
    """
    backtest_config_path, base_strategy_config_path, parameters, trial_id = args

    try:
        # Create temporary strategy config with trial parameters
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix=f"trial_{trial_id}_", delete=False
        ) as temp_config:
            # Load base config and merge with trial parameters
            import yaml

            with open(base_strategy_config_path) as f:
                base_config = yaml.safe_load(f)

            # Deep merge parameters into base config
            for section, params in parameters.items():
                if section in base_config:
                    base_config[section].update(params)
                else:
                    base_config[section] = params

            # Write merged config
            yaml.dump(base_config, temp_config)
            temp_config_path = Path(temp_config.name)

        # Run backtest
        runner = BacktestRunner(
            backtest_config_path=backtest_config_path,
            strategy_config_paths=[temp_config_path],
            run_id=f"trial_{trial_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        )

        results = runner.run()

        # Extract metrics using ReportGenerator
        if results and "engine" in results:
            generator = ReportGenerator()
            metrics = generator.calculate_metrics(
                engine=results["engine"], strategy_id=results.get("strategy_id", "HedgeGridV1")
            )
            return metrics, None
        return None, "Backtest returned no results"

    except Exception as e:
        error_msg = f"Trial {trial_id} failed: {e!s}\n{traceback.format_exc()}"
        logging.exception(error_msg)
        return None, error_msg

    finally:
        # Clean up temporary config file
        try:
            if "temp_config_path" in locals():
                temp_config_path.unlink(missing_ok=True)
        except Exception as cleanup_err:
            logging.warning(f"Failed to cleanup temp config: {cleanup_err}")


@dataclass
class BacktestTask:
    """Represents a single backtest task for parallel execution."""

    trial_id: int
    parameters: dict[str, Any]
    backtest_config_path: Path
    base_strategy_config_path: Path


@dataclass
class BacktestResult:
    """Result from a backtest execution."""

    trial_id: int
    metrics: PerformanceMetrics | None
    error: str | None
    duration_seconds: float


class ParallelBacktestRunner:
    """
    Executes multiple backtests in parallel using ProcessPoolExecutor.

    This runner manages concurrent execution of backtests across multiple
    CPU cores, with progress tracking, error handling, and retry logic.
    It's designed to work with the optimization framework for efficient
    parameter search.

    Attributes
    ----------
    n_workers : int
        Number of parallel workers
    max_retries : int
        Maximum retry attempts for failed backtests
    console : Console
        Rich console for output
    """

    def __init__(self, n_workers: int | None = None, max_retries: int = 3, verbose: bool = True):
        """
        Initialize parallel backtest runner.

        Parameters
        ----------
        n_workers : int, optional
            Number of parallel workers (defaults to CPU count - 1)
        max_retries : int
            Maximum retry attempts for failed backtests
        verbose : bool
            Whether to show progress output
        """
        # Default to CPU count - 1, leaving one core for system
        if n_workers is None:
            n_workers = max(1, mp.cpu_count() - 1)

        self.n_workers = min(n_workers, mp.cpu_count())
        self.max_retries = max_retries
        self.verbose = verbose
        self.console = Console() if verbose else None

        # Set multiprocessing start method to 'spawn' for compatibility
        try:
            mp.set_start_method("spawn", force=True)
        except RuntimeError:
            # Already set, ignore
            pass

    def run_backtests(
        self, tasks: list[BacktestTask], callback: Callable[[BacktestResult], None] | None = None
    ) -> list[BacktestResult]:
        """
        Run multiple backtests in parallel.

        Parameters
        ----------
        tasks : List[BacktestTask]
            List of backtest tasks to execute
        callback : Callable, optional
            Function to call with each result as it completes

        Returns
        -------
        List[BacktestResult]
            Results from all backtests
        """
        results = []
        failed_tasks = []

        if self.verbose and self.console:
            self.console.print(f"[cyan]Running {len(tasks)} backtests with {self.n_workers} workers[/cyan]")

        # Prepare arguments for multiprocessing
        task_args = [
            (
                task.backtest_config_path,
                task.base_strategy_config_path,
                task.parameters,
                task.trial_id,
            )
            for task in tasks
        ]

        # Execute backtests with progress tracking
        with ProcessPoolExecutor(max_workers=self.n_workers) as executor:
            # Submit all tasks
            if self.verbose:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeElapsedColumn(),
                    console=self.console,
                ) as progress:
                    task_id = progress.add_task("[cyan]Running backtests...", total=len(tasks))

                    # Submit tasks and track futures
                    future_to_task = {
                        executor.submit(_run_single_backtest, args): (task, args)
                        for task, args in zip(tasks, task_args, strict=False)
                    }

                    # Process completed tasks
                    for future in as_completed(future_to_task):
                        task, _args = future_to_task[future]
                        start_time = datetime.now()

                        try:
                            metrics, error = future.result(timeout=600)  # 10 minute timeout
                            duration = (datetime.now() - start_time).total_seconds()

                            result = BacktestResult(
                                trial_id=task.trial_id,
                                metrics=metrics,
                                error=error,
                                duration_seconds=duration,
                            )

                            if error and self.max_retries > 0:
                                # Add to retry queue
                                failed_tasks.append(task)
                            else:
                                results.append(result)
                                if callback:
                                    callback(result)

                        except Exception as e:
                            duration = (datetime.now() - start_time).total_seconds()
                            result = BacktestResult(
                                trial_id=task.trial_id,
                                metrics=None,
                                error=str(e),
                                duration_seconds=duration,
                            )
                            results.append(result)
                            if callback:
                                callback(result)

                        progress.update(task_id, advance=1)

            else:
                # Non-verbose execution
                future_to_task = {
                    executor.submit(_run_single_backtest, args): (task, args)
                    for task, args in zip(tasks, task_args, strict=False)
                }

                for future in as_completed(future_to_task):
                    task, _args = future_to_task[future]
                    start_time = datetime.now()

                    try:
                        metrics, error = future.result(timeout=600)
                        duration = (datetime.now() - start_time).total_seconds()

                        result = BacktestResult(
                            trial_id=task.trial_id,
                            metrics=metrics,
                            error=error,
                            duration_seconds=duration,
                        )

                        if error and self.max_retries > 0:
                            failed_tasks.append(task)
                        else:
                            results.append(result)
                            if callback:
                                callback(result)

                    except Exception as e:
                        duration = (datetime.now() - start_time).total_seconds()
                        result = BacktestResult(
                            trial_id=task.trial_id,
                            metrics=None,
                            error=str(e),
                            duration_seconds=duration,
                        )
                        results.append(result)
                        if callback:
                            callback(result)

        # Retry failed tasks
        if failed_tasks and self.max_retries > 0:
            if self.verbose and self.console:
                self.console.print(f"[yellow]Retrying {len(failed_tasks)} failed backtests[/yellow]")

            retry_runner = ParallelBacktestRunner(
                n_workers=self.n_workers,
                max_retries=self.max_retries - 1,  # Decrement retries
                verbose=self.verbose,
            )
            retry_results = retry_runner.run_backtests(failed_tasks, callback)
            results.extend(retry_results)

        # Summary statistics
        if self.verbose and self.console:
            successful = sum(1 for r in results if r.metrics is not None)
            failed = len(results) - successful
            avg_duration = sum(r.duration_seconds for r in results) / max(1, len(results))

            self.console.print("\n[bold]Backtest Execution Summary:[/bold]")
            self.console.print(f"  Total: {len(results)}")
            self.console.print(f"  [green]Successful: {successful}[/green]")
            if failed > 0:
                self.console.print(f"  [red]Failed: {failed}[/red]")
            self.console.print(f"  Avg Duration: {avg_duration:.1f}s")

        return results

    def run_single(self, task: BacktestTask) -> BacktestResult:
        """
        Run a single backtest task.

        This method is useful for debugging or when parallel execution
        is not desired.

        Parameters
        ----------
        task : BacktestTask
            Backtest task to execute

        Returns
        -------
        BacktestResult
            Result from the backtest
        """
        start_time = datetime.now()

        try:
            metrics, error = _run_single_backtest(
                (
                    task.backtest_config_path,
                    task.base_strategy_config_path,
                    task.parameters,
                    task.trial_id,
                )
            )

            duration = (datetime.now() - start_time).total_seconds()

            return BacktestResult(trial_id=task.trial_id, metrics=metrics, error=error, duration_seconds=duration)

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return BacktestResult(trial_id=task.trial_id, metrics=None, error=str(e), duration_seconds=duration)
