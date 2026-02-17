"""Main strategy optimizer orchestrating all optimization components.

This module provides the high-level interface for running parameter
optimization using Optuna, integrating all components of the framework.
"""

import copy
import gc
import logging
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import optuna
import psutil
import yaml
from nautilus_trader.model.enums import OmsType, OrderStatus
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from naut_hedgegrid.config.backtest import BacktestConfigLoader
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.metrics.report import PerformanceMetrics
from naut_hedgegrid.optimization.constraints import ConstraintsValidator, ConstraintThresholds
from naut_hedgegrid.optimization.objective import MultiObjectiveFunction, ObjectiveWeights
from naut_hedgegrid.optimization.param_space import ParameterSpace
from naut_hedgegrid.optimization.results_db import OptimizationResultsDB, OptimizationTrial
from naut_hedgegrid.runners.run_backtest import BacktestRunner


class BacktestTimeoutError(Exception):
    """Raised when a backtest exceeds the timeout limit."""


class BacktestMemoryError(Exception):
    """Raised when a backtest exceeds memory limits."""


def get_memory_usage_gb() -> float:
    """Get current memory usage in GB."""
    process = psutil.Process()
    return process.memory_info().rss / (1024**3)  # Convert bytes to GB


def check_memory_limit(limit_gb: float = 2.0) -> bool:
    """Check if memory usage exceeds limit."""
    current_gb = get_memory_usage_gb()
    return current_gb > limit_gb


class StrategyOptimizer:
    """
    Main orchestrator for strategy parameter optimization.

    This class integrates all optimization components to provide a
    high-level interface for finding optimal strategy parameters
    through Bayesian optimization with Optuna.

    The optimization workflow:
    1. Load base backtest and strategy configurations
    2. Initialize Optuna study with TPE sampler
    3. For each trial:
       a. Sample parameters from search space
       b. Generate temporary strategy config
       c. Run backtest
       d. Calculate performance metrics
       e. Validate constraints
       f. Calculate multi-objective score
       g. Save results to database
    4. Save best parameters to YAML file
    5. Return Optuna study with results

    Attributes
    ----------
    backtest_config_path : Path
        Path to backtest configuration YAML
    base_strategy_config_path : Path
        Path to base strategy configuration YAML
    n_trials : int
        Number of optimization trials to run
    n_jobs : int
        Number of parallel workers for backtests
    study_name : str
        Name of optimization study
    param_space : ParameterSpace
        Parameter search space definition
    objective_func : MultiObjectiveFunction
        Multi-objective scoring function
    constraints : ConstraintsValidator
        Constraints validator
    results_db : OptimizationResultsDB
        Results database
    console : Console
        Rich console for output
    """

    def __init__(
        self,
        backtest_config_path: Path,
        base_strategy_config_path: Path,
        n_trials: int = 100,
        n_jobs: int = 1,
        study_name: str | None = None,
        db_path: Path | None = None,
        param_space: ParameterSpace | None = None,
        objective_weights: ObjectiveWeights | None = None,
        constraint_thresholds: ConstraintThresholds | None = None,
        storage: str | None = None,
        verbose: bool = True,
        backtest_timeout_seconds: int = 300,  # 5 minutes default
        memory_limit_gb: float = 2.0,  # 2GB default
        enable_gc: bool = True,  # Enable aggressive garbage collection
    ):
        """
        Initialize strategy optimizer.

        Parameters
        ----------
        backtest_config_path : Path
            Path to backtest configuration YAML
        base_strategy_config_path : Path
            Path to base strategy configuration YAML
        n_trials : int
            Number of optimization trials to run
        n_jobs : int
            Number of parallel workers (1 = sequential)
        study_name : str, optional
            Name of study (defaults to timestamp-based name)
        db_path : Path, optional
            Path to results database file
        param_space : ParameterSpace, optional
            Custom parameter space (uses defaults if None)
        objective_weights : ObjectiveWeights, optional
            Custom objective weights (uses defaults if None)
        constraint_thresholds : ConstraintThresholds, optional
            Custom constraint thresholds (uses defaults if None)
        storage : str, optional
            Optuna storage URL (e.g., sqlite:///optuna.db)
        verbose : bool
            Whether to show progress output
        backtest_timeout_seconds : int
            Maximum seconds per backtest trial (default 300 = 5 minutes)
        memory_limit_gb : float
            Maximum memory usage in GB before aborting (default 2.0)
        enable_gc : bool
            Enable aggressive garbage collection after each trial
        """
        self.backtest_config_path = Path(backtest_config_path)
        self.base_strategy_config_path = Path(base_strategy_config_path)
        self.n_trials = n_trials
        self.n_jobs = n_jobs
        self.verbose = verbose
        self.backtest_timeout_seconds = backtest_timeout_seconds
        self.memory_limit_gb = memory_limit_gb
        self.enable_gc = enable_gc

        # Generate study name if not provided
        if study_name is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            study_name = f"hedge_grid_opt_{timestamp}"
        self.study_name = study_name

        # Initialize components
        self.param_space = param_space or ParameterSpace()
        self.objective_func = MultiObjectiveFunction(weights=objective_weights)
        self.constraints = ConstraintsValidator(thresholds=constraint_thresholds)
        self.results_db = OptimizationResultsDB(db_path=db_path)
        self.console = Console() if verbose else None

        # Optuna storage
        self.storage = storage

        # Progress tracking (thread-safe for parallel execution)
        self.best_score = float("-inf")
        self.valid_trials = 0
        self.total_trials_run = 0
        self._lock = threading.Lock()

        # Memory tracking
        self.initial_memory_gb = get_memory_usage_gb()
        self.peak_memory_gb = self.initial_memory_gb

        # Thread pool for timeout handling
        # Use more workers to avoid bottlenecks when running parallel trials
        self.executor = ThreadPoolExecutor(max_workers=max(1, self.n_jobs * 2))

        # Load and validate base configs
        self._validate_base_configs()

        # Report initial memory usage
        if self.verbose and self.console:
            self.console.print(f"[dim]Initial Memory:[/dim] {self.initial_memory_gb:.2f} GB")
            self.console.print(f"[dim]Memory Limit:[/dim] {self.memory_limit_gb:.2f} GB")
            self.console.print(
                f"[dim]Timeout:[/dim] {self.backtest_timeout_seconds} seconds per trial"
            )

    def _validate_base_configs(self):
        """Validate that base configuration files are valid."""
        try:
            # Load backtest config
            self.backtest_config = BacktestConfigLoader.load(self.backtest_config_path)

            # Load base strategy config
            self.base_strategy_config = HedgeGridConfigLoader.load(self.base_strategy_config_path)

            # Cache the base config dict to avoid repeated file I/O
            with open(self.base_strategy_config_path) as f:
                self._base_config_dict = yaml.safe_load(f)

            if self.verbose and self.console:
                self.console.print("[green]✓[/green] Base configurations loaded successfully")

        except Exception as e:
            raise ValueError(f"Failed to load base configurations: {e}")

    def optimize(self) -> optuna.Study:
        """
        Run parameter optimization.

        Returns
        -------
        optuna.Study
            Completed Optuna study with results
        """
        if self.verbose and self.console:
            self.console.print(
                "\n[bold cyan]═══════════════════════════════════════════[/bold cyan]"
            )
            self.console.print("[bold cyan]     Parameter Optimization Started[/bold cyan]")
            self.console.print(
                "[bold cyan]═══════════════════════════════════════════[/bold cyan]\n"
            )
            self.console.print(f"[dim]Study Name:[/dim] {self.study_name}")
            self.console.print(f"[dim]Total Trials:[/dim] {self.n_trials}")
            self.console.print(f"[dim]Parallel Jobs:[/dim] {self.n_jobs}")
            self.console.print(f"[dim]Backtest Config:[/dim] {self.backtest_config_path}")
            self.console.print(f"[dim]Base Strategy:[/dim] {self.base_strategy_config_path}\n")

        # Create Optuna study
        study = optuna.create_study(
            study_name=self.study_name,
            direction="maximize",  # Maximize objective score
            sampler=TPESampler(seed=42, n_startup_trials=10),
            pruner=MedianPruner(n_startup_trials=5, n_warmup_steps=0),
            storage=self.storage,
            load_if_exists=True,
        )

        # Suppress Optuna's default logging
        optuna.logging.set_verbosity(optuna.logging.ERROR)

        # Define objective function wrapper
        def objective(trial: optuna.Trial) -> float:
            """Objective function for Optuna trial."""
            trial_start = datetime.now()

            # Increment trial counter at start (thread-safe for parallel execution)
            with self._lock:
                self.total_trials_run += 1
                current_trial_num = self.total_trials_run

            try:
                # Suggest parameters from trial
                parameters = self.param_space.suggest_parameters(trial)

                # Validate parameters
                if not self.param_space.validate_parameters(parameters):
                    return float("-inf")

                # Run backtest with parameters (logging suppression happens inside)
                metrics = self._run_backtest_with_parameters(trial.number, parameters)

                if metrics is None:
                    if self.verbose and self.console:
                        self.console.print(
                            f"[red]✗ Trial {current_trial_num}: Backtest failed[/red]"
                        )
                    return float("-inf")

                # Validate constraints
                is_valid = self.constraints.is_valid(metrics)
                violations = self.constraints.get_violations(metrics)

                # Calculate multi-objective score only if valid
                if is_valid:
                    score = self.objective_func.calculate_score(metrics)
                    with self._lock:
                        self.valid_trials += 1
                        self.best_score = max(score, self.best_score)
                else:
                    # Invalid trials get worst possible score
                    score = float("-inf")

                # Store validation result in Optuna trial for later access
                trial.set_user_attr("is_valid", is_valid)
                trial.set_user_attr("total_trades", metrics.total_trades)
                trial.set_user_attr("sharpe_ratio", metrics.sharpe_ratio)
                trial.set_user_attr("win_rate_pct", metrics.win_rate_pct)

                # Save trial to database
                trial_data = OptimizationTrial(
                    study_name=self.study_name,
                    parameters=parameters,
                    metrics=self._metrics_to_dict(metrics),
                    score=score,
                    is_valid=is_valid,
                    violations=violations,
                    timestamp=trial_start,
                    duration_seconds=(datetime.now() - trial_start).total_seconds(),
                )
                self.results_db.save_trial(trial_data)

                # Log trial result (only significant ones or invalid)
                if self.verbose and self.console:
                    self._log_trial_result_compact(
                        current_trial_num, metrics, score, is_valid, violations
                    )

                return score

            except Exception as e:
                # Save failed trial
                trial_data = OptimizationTrial(
                    study_name=self.study_name,
                    parameters={},
                    metrics={},
                    score=float("-inf"),
                    is_valid=False,
                    violations=[],
                    timestamp=trial_start,
                    duration_seconds=(datetime.now() - trial_start).total_seconds(),
                    error_message=str(e),
                )
                self.results_db.save_trial(trial_data)

                if self.verbose and self.console:
                    self.console.print(
                        f"[red]✗ Trial {current_trial_num} ERROR: {str(e)[:80]}[/red]"
                    )

                return float("-inf")

        # Run optimization with progress bar
        try:
            if self.verbose and self.console:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeElapsedColumn(),
                    TimeRemainingColumn(),
                    console=self.console,
                    transient=False,
                ) as progress:
                    task = progress.add_task(
                        f"[cyan]Optimizing ({self.n_trials} trials)", total=self.n_trials
                    )

                    def callback(study, trial):
                        """Update progress bar after each trial."""
                        progress.update(
                            task,
                            advance=1,
                            description=f"[cyan]Trial {self.total_trials_run}/{self.n_trials} | Best: {self.best_score:.4f} | Valid: {self.valid_trials}/{self.total_trials_run}",
                        )

                    study.optimize(
                        objective,
                        n_trials=self.n_trials,
                        n_jobs=self.n_jobs,
                        show_progress_bar=False,
                        callbacks=[callback],
                    )
            else:
                study.optimize(
                    objective, n_trials=self.n_trials, n_jobs=self.n_jobs, show_progress_bar=False
                )

        except KeyboardInterrupt:
            if self.verbose and self.console:
                self.console.print("\n[yellow]⚠ Optimization interrupted by user[/yellow]")

        # Show final results
        if self.verbose and self.console:
            self._show_final_results(study)

        # Save best parameters to YAML
        self._save_best_parameters(study)

        # Clean up executor
        self._cleanup()

        return study

    def _cleanup(self):
        """Clean up resources after optimization."""
        try:
            # Shutdown executor
            if hasattr(self, "executor"):
                self.executor.shutdown(wait=False)

            # Force garbage collection
            gc.collect()

            # Report final memory
            if self.verbose and self.console:
                final_mem = get_memory_usage_gb()
                self.console.print(f"[dim]Cleanup complete. Memory: {final_mem:.2f} GB[/dim]")
        except Exception as e:
            logging.warning(f"Cleanup error: {e}")

    def _run_backtest_with_timeout(
        self, trial_id: int, parameters: dict[str, Any], timeout_seconds: int
    ) -> PerformanceMetrics | None:
        """
        Run backtest with timeout protection using thread executor.

        This wrapper ensures the backtest completes within the timeout limit.
        """
        future = self.executor.submit(self._run_backtest_core, trial_id, parameters)

        try:
            # Wait for backtest with timeout
            result = future.result(timeout=timeout_seconds)
            return result
        except FuturesTimeoutError:
            # Backtest exceeded timeout
            future.cancel()
            if self.verbose and self.console:
                self.console.print(
                    f"[yellow]⚠ Trial {trial_id}: Timeout after {timeout_seconds}s[/yellow]"
                )
            return None
        except Exception as e:
            # Other errors during backtest
            if self.verbose and self.console:
                self.console.print(f"[red]✗ Trial {trial_id}: Error - {str(e)[:100]}[/red]")
            return None

    def _run_backtest_with_parameters(
        self, trial_id: int, parameters: dict[str, Any]
    ) -> PerformanceMetrics | None:
        """
        Run backtest with given parameters, including timeout and memory protection.

        Parameters
        ----------
        trial_id : int
            Trial identifier
        parameters : Dict[str, Any]
            Parameter dictionary to test

        Returns
        -------
        PerformanceMetrics or None
            Performance metrics if successful, None if failed
        """
        start_time = time.time()
        start_memory_gb = get_memory_usage_gb()

        # Check memory before starting
        if check_memory_limit(self.memory_limit_gb):
            if self.verbose and self.console:
                self.console.print(
                    f"[yellow]⚠ Trial {trial_id}: Memory limit exceeded "
                    f"({start_memory_gb:.2f} GB > {self.memory_limit_gb:.2f} GB)[/yellow]"
                )
            # Force garbage collection
            if self.enable_gc:
                gc.collect()
                time.sleep(0.5)  # Brief pause for memory to settle
                # Check again after GC
                if check_memory_limit(self.memory_limit_gb):
                    return None

        try:
            # Log progress
            if self.verbose and self.console and trial_id % 10 == 0:
                elapsed = time.time() - start_time
                self.console.print(
                    f"[dim]Starting Trial {trial_id} | "
                    f"Memory: {start_memory_gb:.2f} GB | "
                    f"Timeout: {self.backtest_timeout_seconds}s[/dim]"
                )

            # Run backtest with timeout protection
            result = self._run_backtest_with_timeout(
                trial_id, parameters, self.backtest_timeout_seconds
            )

            # Check memory after backtest
            end_memory_gb = get_memory_usage_gb()
            memory_used_gb = end_memory_gb - start_memory_gb

            # Update peak memory
            with self._lock:
                self.peak_memory_gb = max(self.peak_memory_gb, end_memory_gb)

            # Log memory usage if significant
            if memory_used_gb > 0.5:  # More than 500 MB used
                if self.verbose and self.console:
                    self.console.print(
                        f"[yellow]⚠ Trial {trial_id}: High memory usage "
                        f"(+{memory_used_gb:.2f} GB)[/yellow]"
                    )

            # Aggressive garbage collection after each trial
            if self.enable_gc:
                gc.collect()

            # Log completion time
            elapsed_time = time.time() - start_time
            if elapsed_time > 60 and self.verbose:  # Log if took more than 1 minute
                self.console.print(f"[dim]Trial {trial_id} completed in {elapsed_time:.1f}s[/dim]")

            return result

        except Exception as e:
            logging.exception(f"Unexpected error in trial {trial_id}: {e}")
            return None
        finally:
            # Always clean up memory
            if self.enable_gc:
                gc.collect()

    def _run_backtest_core(
        self, trial_id: int, parameters: dict[str, Any]
    ) -> PerformanceMetrics | None:
        """
        Core backtest execution logic without timeout handling.

        This method contains the actual backtest logic and is called
        by the timeout wrapper.

        Parameters
        ----------
        trial_id : int
            Trial identifier
        parameters : Dict[str, Any]
            Parameter dictionary to test

        Returns
        -------
        PerformanceMetrics or None
            Performance metrics if successful, None if failed
        """
        try:
            # Temporarily suppress logging during backtest
            # Save original backtest config logging settings
            original_log_level = self.backtest_config.output.log_level
            original_log_level_file = self.backtest_config.output.log_level_file

            # Set to ERROR to suppress most logs (CRITICAL not supported by Rust backend)
            self.backtest_config.output.log_level = "ERROR"
            self.backtest_config.output.log_level_file = None  # Disable file logging completely

            # Create temporary strategy config with trial parameters
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", prefix=f"trial_{trial_id}_", delete=False
            ) as temp_config:
                # Use cached base config dict (avoid file I/O)
                base_config = copy.deepcopy(self._base_config_dict)

                # Deep merge parameters into base config
                for section, params in parameters.items():
                    if section in base_config:
                        # No special handling needed - max_position_pct is already a decimal fraction
                        base_config[section].update(params)
                    else:
                        # Handle new sections
                        base_config[section] = params

                # Force optimization mode to reduce logging and retries
                if "execution" not in base_config:
                    base_config["execution"] = {}
                base_config["execution"]["optimization_mode"] = True

                # Write merged config
                yaml.dump(base_config, temp_config)
                temp_config_path = Path(temp_config.name)

            # Create HedgeGridV1Config that points to temporary file
            # First load the temp config to extract instrument_id
            temp_hedge_grid_cfg = HedgeGridConfigLoader.load(temp_config_path)

            # Import HedgeGridV1Config
            from naut_hedgegrid.strategies.hedge_grid_v1 import HedgeGridV1Config

            # Create Nautilus strategy config pointing to temp file
            strategy_config = HedgeGridV1Config(
                instrument_id=temp_hedge_grid_cfg.strategy.instrument_id,
                hedge_grid_config_path=str(temp_config_path),
                oms_type=OmsType.HEDGING,
            )

            # Run backtest
            runner = BacktestRunner(
                backtest_config=self.backtest_config, strategy_configs=[strategy_config]
            )

            try:
                # Setup catalog and run backtest
                catalog = runner.setup_catalog()
                engine, data = runner.run(catalog)

                # Clean up temporary config
                temp_config_path.unlink(missing_ok=True)

            finally:
                # Restore original logging settings
                self.backtest_config.output.log_level = original_log_level
                self.backtest_config.output.log_level_file = original_log_level_file

            # Extract metrics
            if engine:
                try:
                    # Get portfolio and account
                    portfolio = engine.portfolio

                    # Get venue - default to BINANCE since it's hardcoded in our configs
                    venue = Venue("BINANCE")
                    account = portfolio.account(venue)

                    # Get currency
                    currency = Currency.from_str("USDT")

                    # Extract starting capital from config
                    starting_capital = 10000.0  # Default
                    if (
                        self.backtest_config.venues
                        and self.backtest_config.venues[0].starting_balances
                    ):
                        starting_balance = self.backtest_config.venues[0].starting_balances[0]
                        starting_capital = float(starting_balance.total)

                    # ==================================================================
                    # 1. Extract actual PnL from portfolio
                    # ==================================================================
                    total_pnl = 0.0
                    total_realized_pnl = 0.0
                    total_unrealized_pnl = 0.0

                    if account:
                        # Get final balance
                        final_balance = (
                            float(account.balance_total(currency).as_double())
                            if account.balance_total(currency)
                            else starting_capital
                        )
                        total_pnl = final_balance - starting_capital

                        # Try to get realized and unrealized PnL using venue
                        # These methods return dict of {Currency: Money}
                        realized_pnls_dict = portfolio.realized_pnls(venue)
                        if realized_pnls_dict and currency in realized_pnls_dict:
                            total_realized_pnl = float(realized_pnls_dict[currency].as_double())

                        unrealized_pnls_dict = portfolio.unrealized_pnls(venue)
                        if unrealized_pnls_dict and currency in unrealized_pnls_dict:
                            total_unrealized_pnl = float(unrealized_pnls_dict[currency].as_double())

                    # If we didn't get PnL from account, try BacktestResult
                    if total_pnl == 0.0:
                        try:
                            result = engine.get_result()
                            if result and result.stats_pnls:
                                # stats_pnls is dict[str, dict[str, float]] - strategy_id -> pnl dict
                                for strategy_pnls in result.stats_pnls.values():
                                    if "total" in strategy_pnls:
                                        total_pnl = strategy_pnls["total"]
                                    elif "pnl" in strategy_pnls:
                                        total_pnl = strategy_pnls["pnl"]
                                    break
                        except Exception:
                            pass

                    # Calculate return percentage
                    total_return_pct = (
                        (total_pnl / starting_capital * 100) if starting_capital > 0 else 0.0
                    )

                    # ==================================================================
                    # 2. Extract trade statistics from positions
                    # ==================================================================
                    positions_closed = engine.cache.positions_closed()
                    positions_open = engine.cache.positions_open()

                    total_trades = len(positions_closed)  # Count closed positions as trades
                    winning_trades = 0
                    losing_trades = 0
                    total_win = 0.0
                    total_loss = 0.0

                    # Analyze closed positions for win/loss statistics
                    for pos in positions_closed:
                        if hasattr(pos, "realized_pnl") and pos.realized_pnl:
                            pnl = float(pos.realized_pnl.as_double())
                            if pnl > 0:
                                winning_trades += 1
                                total_win += pnl
                            elif pnl < 0:
                                losing_trades += 1
                                total_loss += abs(pnl)

                    # If no closed positions, count filled orders as proxy for activity
                    if total_trades == 0:
                        all_orders = engine.cache.orders()
                        filled_orders = [o for o in all_orders if o.status == OrderStatus.FILLED]
                        # Count filled orders but don't use for win/loss (we have no PnL data)
                        total_trades = len(filled_orders)
                        # Don't fake win rate - leave at 0 if no position data
                        winning_trades = 0
                        losing_trades = 0

                    # Calculate win rate and profit factor
                    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
                    profit_factor = (
                        (total_win / total_loss)
                        if total_loss > 0
                        else (total_win if total_win > 0 else 0.0)
                    )

                    avg_win = total_win / winning_trades if winning_trades > 0 else 0.0
                    avg_loss = total_loss / losing_trades if losing_trades > 0 else 0.0
                    avg_trade_pnl = total_realized_pnl / total_trades if total_trades > 0 else 0.0

                    # ==================================================================
                    # 3. Calculate risk metrics (Sharpe, Sortino, Calmar)
                    # ==================================================================
                    sharpe_ratio = 0.0
                    sortino_ratio = 0.0
                    calmar_ratio = 0.0
                    max_drawdown_pct = 0.0

                    # Try to get returns data for Sharpe calculation
                    try:
                        result = engine.get_result()
                        if result and result.stats_returns:
                            # stats_returns might have daily returns or other return metrics
                            returns_data = result.stats_returns

                            # Calculate Sharpe if we have return and volatility data
                            if "return" in returns_data and "volatility" in returns_data:
                                annual_return = returns_data["return"]
                                volatility = returns_data["volatility"]
                                risk_free_rate = 0.05  # 5% annual
                                if volatility > 0:
                                    sharpe_ratio = (annual_return - risk_free_rate) / volatility
                    except Exception:
                        pass

                    # Fallback Sharpe calculation based on trade outcomes
                    if sharpe_ratio == 0.0 and total_trades >= 5:
                        # Calculate returns series from position PnLs
                        position_returns = []
                        for pos in positions_closed:
                            if hasattr(pos, "realized_pnl") and pos.realized_pnl:
                                pnl = float(pos.realized_pnl.as_double())
                                # Normalize by starting capital to get return
                                ret = pnl / starting_capital
                                position_returns.append(ret)

                        if len(position_returns) >= 5:
                            returns_array = np.array(position_returns)
                            mean_return = np.mean(returns_array)
                            std_return = np.std(returns_array)

                            if std_return > 0:
                                # Annualize assuming daily positions
                                periods_per_year = 365
                                annualized_mean = mean_return * periods_per_year
                                annualized_std = std_return * np.sqrt(periods_per_year)
                                risk_free_rate = 0.05

                                sharpe_ratio = (annualized_mean - risk_free_rate) / annualized_std
                                sharpe_ratio = max(
                                    -3.0, min(3.0, sharpe_ratio)
                                )  # Cap for stability

                                # Sortino ratio (downside deviation)
                                negative_returns = returns_array[returns_array < 0]
                                if len(negative_returns) > 0:
                                    downside_std = np.std(negative_returns) * np.sqrt(
                                        periods_per_year
                                    )
                                    if downside_std > 0:
                                        sortino_ratio = (
                                            annualized_mean - risk_free_rate
                                        ) / downside_std
                                        sortino_ratio = max(-3.0, min(3.0, sortino_ratio))

                    # Calculate max drawdown from equity curve simulation
                    if len(positions_closed) > 0:
                        equity_curve = [starting_capital]
                        current_equity = starting_capital

                        # Build equity curve from position PnLs
                        for pos in sorted(
                            positions_closed,
                            key=lambda p: p.ts_closed if hasattr(p, "ts_closed") else 0,
                        ):
                            if hasattr(pos, "realized_pnl") and pos.realized_pnl:
                                pnl = float(pos.realized_pnl.as_double())
                                current_equity += pnl
                                equity_curve.append(current_equity)

                        # Calculate drawdown
                        equity_array = np.array(equity_curve)
                        running_max = np.maximum.accumulate(equity_array)
                        drawdown = (equity_array - running_max) / running_max * 100
                        max_drawdown_pct = abs(float(np.min(drawdown)))

                        # Calmar ratio
                        if max_drawdown_pct > 0:
                            # Annualize return
                            days_in_backtest = 30  # Approximate from config
                            annualized_return = total_return_pct * (365 / days_in_backtest)
                            calmar_ratio = annualized_return / max_drawdown_pct

                    # ==================================================================
                    # 4. Count actual orders and fills
                    # ==================================================================
                    all_orders = engine.cache.orders()
                    total_orders = len(all_orders)
                    filled_orders = [o for o in all_orders if o.status == OrderStatus.FILLED]

                    # ==================================================================
                    # 5. Create metrics object with real extracted values
                    # ==================================================================
                    metrics = PerformanceMetrics(
                        # Returns - REAL VALUES
                        total_pnl=total_pnl,
                        total_return_pct=total_return_pct,
                        annualized_return_pct=total_return_pct * 12,  # Approximate annual
                        # Risk metrics - CALCULATED FROM ACTUAL DATA
                        sharpe_ratio=sharpe_ratio,
                        sortino_ratio=sortino_ratio,
                        calmar_ratio=calmar_ratio,
                        max_drawdown_pct=max_drawdown_pct,
                        max_drawdown_duration_days=0.0,  # Would need tick-by-tick equity curve
                        # Trade metrics - FROM ACTUAL POSITIONS
                        total_trades=total_trades,
                        winning_trades=winning_trades,
                        losing_trades=losing_trades,
                        win_rate_pct=win_rate,
                        avg_win=avg_win,
                        avg_loss=avg_loss,
                        profit_factor=profit_factor,
                        avg_trade_pnl=avg_trade_pnl,
                        # Execution metrics - Set to 0 for now (would need fill analysis)
                        maker_fill_ratio=0.0,
                        avg_slippage_bps=0.0,
                        total_fees_paid=0.0,
                        # Funding metrics - Set to 0 (would need funding event tracking)
                        funding_paid=0.0,
                        funding_received=0.0,
                        net_funding_pnl=0.0,
                        # Exposure metrics - Set to 0 (would need position tracking)
                        avg_long_exposure=0.0,
                        avg_short_exposure=0.0,
                        max_long_exposure=0.0,
                        max_short_exposure=0.0,
                        time_in_market_pct=0.0,
                        # Ladder metrics - Set to 0 (strategy-specific)
                        avg_ladder_depth_long=0.0,
                        avg_ladder_depth_short=0.0,
                        ladder_fill_rate_pct=(len(filled_orders) / total_orders * 100)
                        if total_orders > 0
                        else 0.0,
                        # MAE/MFE - Set to 0 (would need tick-by-tick position tracking)
                        avg_mae_pct=0.0,
                        avg_mfe_pct=0.0,
                    )

                    # Log extraction success with key metrics
                    logging.info(
                        f"Successfully extracted metrics - PnL: ${total_pnl:.2f}, "
                        f"Return: {total_return_pct:.2f}%, Trades: {total_trades}, "
                        f"Win Rate: {win_rate:.1f}%, Sharpe: {sharpe_ratio:.2f}"
                    )

                    return metrics

                except Exception as e:
                    # Log the actual error for debugging
                    logging.error(f"Failed to extract metrics: {e}", exc_info=True)

                    # Return None to indicate metrics extraction failure
                    # This will cause the trial to get float("-inf") score
                    return None

            # No engine means backtest completely failed
            return None

        except Exception as e:
            logging.exception(f"Backtest failed for trial {trial_id}: {e}")
            return None

    def _metrics_to_dict(self, metrics: PerformanceMetrics) -> dict[str, float]:
        """Convert PerformanceMetrics to dictionary."""
        return {
            "total_pnl": metrics.total_pnl,
            "total_return_pct": metrics.total_return_pct,
            "annualized_return_pct": metrics.annualized_return_pct,
            "sharpe_ratio": metrics.sharpe_ratio,
            "sortino_ratio": metrics.sortino_ratio,
            "calmar_ratio": metrics.calmar_ratio,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "total_trades": metrics.total_trades,
            "win_rate_pct": metrics.win_rate_pct,
            "profit_factor": metrics.profit_factor,
        }

    def _log_trial_result_compact(
        self,
        trial_number: int,
        metrics: PerformanceMetrics,
        score: float,
        is_valid: bool,
        violations: list[str],
    ):
        """Log trial result in compact format (only show important trials)."""
        if not self.console:
            return

        # Only log if: invalid, has violations, or is a new best score
        is_new_best = score >= self.best_score and is_valid
        should_log = not is_valid or violations or is_new_best

        if not should_log:
            return

        status = "[green]✓[/green]" if is_valid else "[red]✗[/red]"

        if is_new_best:
            self.console.print(
                f"{status} [bold green]NEW BEST[/bold green] Trial {trial_number}: "
                f"Score={score:.4f} | Sharpe={metrics.sharpe_ratio:.2f} | "
                f"PF={metrics.profit_factor:.2f} | DD={metrics.max_drawdown_pct:.1f}% | "
                f"Trades={metrics.total_trades} | WR={metrics.win_rate_pct:.1f}%"
            )
        elif not is_valid:
            violation_str = ", ".join(violations[:2])  # Show first 2 violations
            if len(violations) > 2:
                violation_str += f" (+{len(violations) - 2} more)"
            self.console.print(f"{status} Trial {trial_number}: Invalid - {violation_str}")

    def _log_trial_result(
        self,
        trial_number: int,
        metrics: PerformanceMetrics,
        score: float,
        is_valid: bool,
        violations: list[str],
    ):
        """Log trial result to console (verbose mode)."""
        if not self.console:
            return

        status = "[green]✓[/green]" if is_valid else "[red]✗[/red]"
        self.console.print(f"\n{status} Trial {trial_number} - Score: {score:.4f}")

        # Key metrics
        table = Table(show_header=False, box=None)
        table.add_row("Sharpe", f"{metrics.sharpe_ratio:.2f}")
        table.add_row("Profit Factor", f"{metrics.profit_factor:.2f}")
        table.add_row("Calmar", f"{metrics.calmar_ratio:.2f}")
        table.add_row("Max DD", f"{metrics.max_drawdown_pct:.1f}%")
        table.add_row("Trades", str(metrics.total_trades))
        table.add_row("Win Rate", f"{metrics.win_rate_pct:.1f}%")

        self.console.print(table)

        if violations:
            self.console.print(f"[yellow]Violations: {', '.join(violations)}[/yellow]")

    def _show_final_results(self, study: optuna.Study):
        """Show final optimization results."""
        if not self.console:
            return

        self.console.print("\n[bold cyan]═══════════════════════════════════════════[/bold cyan]")
        self.console.print("[bold cyan]     Optimization Complete! 🎉[/bold cyan]")
        self.console.print("[bold cyan]═══════════════════════════════════════════[/bold cyan]\n")

        # Get best trial
        best_trial = study.best_trial

        self.console.print(f"[bold green]Best Trial:[/bold green] #{best_trial.number}")
        self.console.print(f"[bold green]Best Score:[/bold green] {best_trial.value:.4f}\n")

        # Report memory usage
        final_memory_gb = get_memory_usage_gb()
        memory_increase_gb = final_memory_gb - self.initial_memory_gb
        self.console.print("[bold cyan]Memory Usage:[/bold cyan]")
        self.console.print(f"  Initial: {self.initial_memory_gb:.2f} GB")
        self.console.print(f"  Peak: {self.peak_memory_gb:.2f} GB")
        self.console.print(f"  Final: {final_memory_gb:.2f} GB")
        self.console.print(f"  Increase: {memory_increase_gb:+.2f} GB\n")

        # Show best parameters in a nice table
        table = Table(
            title="🏆 Optimized Parameters", show_header=True, header_style="bold magenta"
        )
        table.add_column("Parameter", style="cyan", no_wrap=True)
        table.add_column("Value", style="green", justify="right")

        for key, value in sorted(best_trial.params.items()):
            if isinstance(value, float):
                formatted_value = f"{value:.4f}"
            else:
                formatted_value = str(value)
            table.add_row(key, formatted_value)

        self.console.print(table)

        # Show study statistics
        stats = self.results_db.get_study_stats(self.study_name)
        if stats and "error" not in stats:
            self.console.print("\n[bold cyan]Study Statistics:[/bold cyan]")

            stats_table = Table(show_header=False, box=None, padding=(0, 2))
            stats_table.add_column("Metric", style="dim")
            stats_table.add_column("Value", style="bold")

            stats_table.add_row("Total Trials", str(stats["total_trials"]))
            stats_table.add_row("Valid Trials", f"[green]{stats['valid_trials']}[/green]")
            stats_table.add_row("Validity Rate", f"{stats['validity_rate']:.1%}")
            stats_table.add_row("Best Score", f"[bold green]{stats['best_score']:.4f}[/bold green]")
            stats_table.add_row("Average Score", f"{stats['avg_score']:.4f}")

            self.console.print(stats_table)

    def _save_best_parameters(self, study: optuna.Study):
        """Save best parameters to YAML file."""
        try:
            # Get best parameters
            best_params = self.param_space.suggest_parameters(study.best_trial)

            # Load base config
            with open(self.base_strategy_config_path) as f:
                base_config = yaml.safe_load(f)

            # Merge best parameters
            for section, params in best_params.items():
                if section in base_config:
                    base_config[section].update(params)
                else:
                    base_config[section] = params

            # Save optimized config
            output_path = Path(f"configs/strategies/{self.study_name}_best.yaml")
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w") as f:
                yaml.dump(base_config, f, default_flow_style=False, sort_keys=False)

            if self.verbose and self.console:
                self.console.print(f"\n[green]✓[/green] Best parameters saved to: {output_path}")

        except Exception as e:
            logging.exception(f"Failed to save best parameters: {e}")

    def export_results(self, output_path: Path):
        """
        Export optimization results to CSV.

        Parameters
        ----------
        output_path : Path
            Path for output CSV file
        """
        # Ensure output directory exists
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.results_db.export_to_csv(self.study_name, output_path)

        if self.verbose and self.console:
            self.console.print(f"[green]✓[/green] Results exported to: {output_path}")
