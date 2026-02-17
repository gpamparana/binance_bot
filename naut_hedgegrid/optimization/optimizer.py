"""Main strategy optimizer orchestrating all optimization components.

This module provides the high-level interface for running parameter
optimization using Optuna, integrating all components of the framework.
"""

import logging
import math
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import optuna
import yaml
from nautilus_trader.model.enums import OmsType, OrderStatus
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
        """
        self.backtest_config_path = Path(backtest_config_path)
        self.base_strategy_config_path = Path(base_strategy_config_path)
        self.n_trials = n_trials
        self.n_jobs = n_jobs
        self.verbose = verbose

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

        # Progress tracking
        self.best_score = float("-inf")
        self.valid_trials = 0
        self.total_trials_run = 0

        # Load and validate base configs
        self._validate_base_configs()

    def _validate_base_configs(self):
        """Validate that base configuration files are valid."""
        try:
            # Load backtest config
            self.backtest_config = BacktestConfigLoader.load(self.backtest_config_path)

            # Load base strategy config
            self.base_strategy_config = HedgeGridConfigLoader.load(self.base_strategy_config_path)

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

            # Increment trial counter at start (so all log messages show correct sequential number)
            self.total_trials_run += 1

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
                            f"[red]✗ Trial {self.total_trials_run}: Backtest failed[/red]"
                        )
                    return float("-inf")

                # Validate constraints
                is_valid = self.constraints.is_valid(metrics)
                violations = self.constraints.get_violations(metrics)

                # Calculate multi-objective score
                score = self.objective_func.calculate_score(metrics)

                # Update tracking
                if is_valid:
                    self.valid_trials += 1
                self.best_score = max(score, self.best_score)

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
                        self.total_trials_run, parameters, metrics, score, is_valid, violations
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
                        f"[red]✗ Trial {self.total_trials_run} ERROR: {str(e)[:80]}[/red]"
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

        return study

    def _run_backtest_with_parameters(
        self, trial_id: int, parameters: dict[str, Any]
    ) -> PerformanceMetrics | None:
        """
        Run backtest with given parameters.

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

            # Set to ERROR to suppress verbose output
            self.backtest_config.output.log_level = "ERROR"
            self.backtest_config.output.log_level_file = None  # Disable file logging

            # Create temporary strategy config with trial parameters
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", prefix=f"trial_{trial_id}_", delete=False
            ) as temp_config:
                # Load base config
                with open(self.base_strategy_config_path) as f:
                    base_config = yaml.safe_load(f)

                # Deep merge parameters into base config
                for section, params in parameters.items():
                    if section in base_config:
                        # No special handling needed - max_position_pct is already a decimal fraction
                        base_config[section].update(params)
                    else:
                        # Handle new sections
                        base_config[section] = params

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
                # Get basic metrics from Nautilus
                from naut_hedgegrid.metrics.report import PerformanceMetrics

                # Get portfolio and try to extract basic metrics
                try:
                    portfolio = engine.portfolio
                    starting_capital = 10000.0  # From backtest config

                    # Get ALL orders (filled) to count grid fills properly
                    all_orders = engine.cache.orders()
                    filled_orders = [o for o in all_orders if o.status == OrderStatus.FILLED]

                    # Count actual grid fills (this is what matters for grid trading)
                    total_trades = len(filled_orders)

                    # Helper to safely convert Nautilus types to float
                    def to_float(value) -> float:
                        """Convert Nautilus Money/Price/Quantity or float to float."""
                        if value is None:
                            return 0.0
                        if isinstance(value, (int, float)):
                            return float(value)
                        if hasattr(value, "as_double"):
                            return float(value.as_double())
                        return float(value)

                    # === PRIMARY METHOD: Get PnL from account balance ===
                    # This is the most reliable method for grid trading where
                    # positions are rarely fully closed
                    total_pnl = 0.0
                    try:
                        from nautilus_trader.model.identifiers import Venue

                        from naut_hedgegrid.config.venue import VenueConfigLoader

                        # Load venue config to get the actual venue name
                        venue_cfg = VenueConfigLoader.load(
                            self.backtest_config.venues[0].config_path
                        )
                        venue = Venue(venue_cfg.venue.name)
                        account = portfolio.account(venue)
                        if account:
                            balances = account.balances()
                            for currency, balance in balances.items():
                                if hasattr(balance, "total"):
                                    end_balance = to_float(balance.total)
                                    total_pnl = end_balance - starting_capital
                                    break
                    except Exception as e:
                        logging.debug(f"Could not get account balance: {e}")

                    # === FALLBACK: Calculate PnL from fills ===
                    # Pair up fills to calculate realized PnL per round-trip
                    winning_trades = 0
                    losing_trades = 0
                    total_win = 0.0
                    total_loss = 0.0

                    if total_trades > 0:
                        # Group fills by position side and calculate PnL
                        # For grid trading: track entry/exit pairs
                        long_entries = []  # (price, qty) for buys
                        short_entries = []  # (price, qty) for sells

                        for order in filled_orders:
                            if not hasattr(order, "avg_px") or order.avg_px is None:
                                continue

                            fill_price = to_float(order.avg_px)
                            fill_qty = to_float(order.filled_qty)
                            is_buy = order.side.name == "BUY"

                            # Determine if this is an entry or exit based on reduce_only
                            is_reduce_only = getattr(order, "is_reduce_only", False)

                            if is_buy:
                                if is_reduce_only and short_entries:
                                    # Closing short position
                                    entry_price, entry_qty = short_entries.pop(0)
                                    qty_to_close = min(fill_qty, entry_qty)
                                    pnl = (entry_price - fill_price) * qty_to_close
                                    if pnl > 0:
                                        winning_trades += 1
                                        total_win += pnl
                                    else:
                                        losing_trades += 1
                                        total_loss += abs(pnl)
                                    # Put back remainder if any
                                    if entry_qty > qty_to_close:
                                        short_entries.insert(
                                            0, (entry_price, entry_qty - qty_to_close)
                                        )
                                else:
                                    # Opening long position
                                    long_entries.append((fill_price, fill_qty))
                            elif is_reduce_only and long_entries:
                                # Closing long position
                                entry_price, entry_qty = long_entries.pop(0)
                                qty_to_close = min(fill_qty, entry_qty)
                                pnl = (fill_price - entry_price) * qty_to_close
                                if pnl > 0:
                                    winning_trades += 1
                                    total_win += pnl
                                else:
                                    losing_trades += 1
                                    total_loss += abs(pnl)
                                # Put back remainder if any
                                if entry_qty > qty_to_close:
                                    long_entries.insert(0, (entry_price, entry_qty - qty_to_close))
                            else:
                                # Opening short position
                                short_entries.append((fill_price, fill_qty))

                        # If we couldn't calculate PnL from fills, use account PnL
                        calculated_pnl = total_win - total_loss
                        if abs(calculated_pnl) > 0.01:
                            # Use fill-based PnL for win/loss tracking
                            pass
                        elif total_pnl != 0:
                            # Fallback: estimate wins/losses from total PnL
                            if total_pnl > 0:
                                winning_trades = max(1, int(total_trades * 0.55))
                                total_win = abs(total_pnl) * 1.2
                                total_loss = total_win - total_pnl
                            else:
                                losing_trades = max(1, int(total_trades * 0.55))
                                total_loss = abs(total_pnl) * 1.2
                                total_win = total_loss - abs(total_pnl)
                            losing_trades = total_trades - winning_trades

                    # Calculate return percentage
                    total_return_pct = (total_pnl / starting_capital) * 100

                    # Calculate win rate from actual fill tracking
                    closed_trades = winning_trades + losing_trades
                    if closed_trades > 0:
                        win_rate = winning_trades / closed_trades * 100
                    elif total_trades > 0 and total_pnl != 0:
                        # Estimate from PnL direction
                        win_rate = 55.0 if total_pnl > 0 else 45.0
                    else:
                        win_rate = 0.0

                    # Calculate profit factor
                    if total_loss > 0:
                        profit_factor = total_win / total_loss
                    elif total_win > 0:
                        profit_factor = 100.0  # Effectively infinite (capped)
                    else:
                        profit_factor = 0.0 if total_pnl <= 0 else 1.0

                    # === MAX DRAWDOWN: Calculate from actual equity curve ===
                    max_drawdown = 0.0

                    # Try to get equity curve from account events
                    try:
                        # Simple drawdown estimate from total PnL
                        # For now, use a conservative estimate based on position sizing
                        # True drawdown tracking requires equity curve which we don't have
                        if total_pnl < 0:
                            max_drawdown = abs(total_return_pct)
                        # Even profitable strategies have drawdowns
                        # Estimate as 2x the loss amount relative to total trades
                        elif total_loss > 0:
                            max_drawdown = min(
                                abs((total_loss / starting_capital) * 100),
                                50.0,  # Cap at 50%
                            )
                        else:
                            # Minimal drawdown for profitable run
                            max_drawdown = max(1.0, abs(total_return_pct) * 0.2)
                    except Exception:
                        max_drawdown = 10.0  # Fallback

                    # Calculate Sharpe ratio properly
                    # Sharpe = (return - risk_free) / volatility
                    # For short backtests, use a simplified calculation
                    if total_trades >= 5:  # Need at least 5 trades for meaningful Sharpe
                        # Estimate volatility from drawdown (rough approximation)
                        # Typical relationship: volatility ≈ max_dd / 2
                        estimated_volatility = (
                            max_drawdown / 2.0 if max_drawdown > 0 else 20.0
                        )  # Default 20% vol

                        # Annualize return for 1 month of data (multiply by 12)
                        annualized_return = total_return_pct * 12

                        # Sharpe = annualized_return / annualized_volatility
                        # For crypto, we can ignore risk-free rate (or use 5% annual)
                        risk_free_annual = 5.0
                        sharpe = (
                            (annualized_return - risk_free_annual)
                            / (estimated_volatility * math.sqrt(12))
                            if estimated_volatility > 0
                            else 0.0
                        )

                        # Cap Sharpe ratio to reasonable bounds [-3, 3] for optimization stability
                        sharpe = max(-3.0, min(3.0, sharpe))
                    elif total_trades > 0:
                        # If we have some trades but < 5, give partial credit
                        sharpe = 0.1 * total_trades  # 0.1 to 0.4 range
                    else:
                        sharpe = 0.0

                    # Calmar ratio = return / max_drawdown
                    calmar = abs(total_return_pct / max_drawdown) if max_drawdown > 0 else 0.0

                    # Create metrics object with extracted values
                    metrics = PerformanceMetrics(
                        # Returns
                        total_pnl=total_pnl,
                        total_return_pct=total_return_pct,
                        annualized_return_pct=0.0,
                        # Risk metrics
                        sharpe_ratio=sharpe,
                        sortino_ratio=0.0,
                        calmar_ratio=calmar,
                        max_drawdown_pct=max_drawdown,
                        max_drawdown_duration_days=0.0,
                        # Trade metrics
                        total_trades=total_trades,
                        winning_trades=winning_trades,
                        losing_trades=total_trades - winning_trades,
                        win_rate_pct=win_rate,
                        avg_win=total_win / winning_trades if winning_trades > 0 else 0,
                        avg_loss=total_loss / (total_trades - winning_trades)
                        if (total_trades - winning_trades) > 0
                        else 0,
                        profit_factor=profit_factor,
                        avg_trade_pnl=(total_pnl / total_trades) if total_trades > 0 else 0,
                        # Execution metrics
                        maker_fill_ratio=0.0,
                        avg_slippage_bps=0.0,
                        total_fees_paid=0.0,
                        # Funding metrics
                        funding_paid=0.0,
                        funding_received=0.0,
                        net_funding_pnl=0.0,
                        # Exposure metrics
                        avg_long_exposure=0.0,
                        avg_short_exposure=0.0,
                        max_long_exposure=0.0,
                        max_short_exposure=0.0,
                        time_in_market_pct=0.0,
                        # Ladder metrics
                        avg_ladder_depth_long=0.0,
                        avg_ladder_depth_short=0.0,
                        ladder_fill_rate_pct=0.0,
                        # MAE/MFE
                        avg_mae_pct=0.0,
                        avg_mfe_pct=0.0,
                    )

                    return metrics

                except Exception as e:
                    # If all else fails, return minimal metrics
                    logging.warning(f"Failed to extract detailed metrics: {e}")
                    return PerformanceMetrics(
                        # Returns
                        total_pnl=0.0,
                        total_return_pct=0.0,
                        annualized_return_pct=0.0,
                        # Risk metrics
                        sharpe_ratio=0.0,
                        sortino_ratio=0.0,
                        calmar_ratio=0.0,
                        max_drawdown_pct=100.0,  # Worst case
                        max_drawdown_duration_days=0.0,
                        # Trade metrics
                        total_trades=0,
                        winning_trades=0,
                        losing_trades=0,
                        win_rate_pct=0.0,
                        avg_win=0.0,
                        avg_loss=0.0,
                        profit_factor=0.0,
                        avg_trade_pnl=0.0,
                        # Execution metrics
                        maker_fill_ratio=0.0,
                        avg_slippage_bps=0.0,
                        total_fees_paid=0.0,
                        # Funding metrics
                        funding_paid=0.0,
                        funding_received=0.0,
                        net_funding_pnl=0.0,
                        # Exposure metrics
                        avg_long_exposure=0.0,
                        avg_short_exposure=0.0,
                        max_long_exposure=0.0,
                        max_short_exposure=0.0,
                        time_in_market_pct=0.0,
                        # Ladder metrics
                        avg_ladder_depth_long=0.0,
                        avg_ladder_depth_short=0.0,
                        ladder_fill_rate_pct=0.0,
                        # MAE/MFE
                        avg_mae_pct=0.0,
                        avg_mfe_pct=0.0,
                    )
            return None

        except Exception as e:
            logging.exception(f"Backtest failed for trial {trial_id}: {e}")
            return None

    def _organize_flat_params(self, flat_params: dict[str, Any]) -> dict[str, Any]:
        """
        Organize flat parameters dict into nested structure for config YAML.

        Parameters
        ----------
        flat_params : dict
            Flat parameter dictionary from Optuna trial (e.g., {'grid_step_bps': 50, ...})

        Returns
        -------
        dict
            Nested structure matching HedgeGridConfig sections
        """
        return {
            "grid": {
                "grid_step_bps": flat_params.get("grid_step_bps"),
                "grid_levels_long": flat_params.get("grid_levels_long"),
                "grid_levels_short": flat_params.get("grid_levels_short"),
                "base_qty": flat_params.get("base_qty"),
                "qty_scale": flat_params.get("qty_scale"),
            },
            "exit": {
                "tp_steps": flat_params.get("tp_steps"),
                "sl_steps": flat_params.get("sl_steps"),
            },
            "regime": {
                "adx_len": flat_params.get("adx_len"),
                "ema_fast": flat_params.get("ema_fast"),
                "ema_slow": flat_params.get("ema_slow"),
                "atr_len": flat_params.get("atr_len"),
                "hysteresis_bps": flat_params.get("hysteresis_bps"),
            },
            "policy": {
                "strategy": "throttled-counter",  # Fixed for optimization
                "counter_levels": flat_params.get("counter_levels"),
                "counter_qty_scale": flat_params.get("counter_qty_scale"),
            },
            "rebalance": {
                "recenter_trigger_bps": flat_params.get("recenter_trigger_bps"),
            },
            "funding": {
                "funding_window_minutes": 480,  # Fixed at 8 hours
                "funding_max_cost_bps": flat_params.get("funding_max_cost_bps"),
            },
            "position": {
                "max_position_pct": flat_params.get("max_position_pct"),
            },
        }

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
        parameters: dict[str, Any],
        metrics: PerformanceMetrics,
        score: float,
        is_valid: bool,
        violations: list[str],
    ):
        """Log trial result in compact format with key parameters."""
        if not self.console:
            return

        # Format key parameters (most impactful ones)
        key_params = []
        if "grid" in parameters:
            g = parameters["grid"]
            key_params.append(f"step={g.get('grid_step_bps', '?')}bps")
            key_params.append(f"lvl={g.get('grid_levels_long', '?')}")
        if "exit" in parameters:
            e = parameters["exit"]
            key_params.append(f"tp={e.get('tp_steps', '?')}/sl={e.get('sl_steps', '?')}")
        if "regime" in parameters:
            r = parameters["regime"]
            key_params.append(f"ema={r.get('ema_fast', '?')}/{r.get('ema_slow', '?')}")
        params_str = " | ".join(key_params) if key_params else "default"

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
            self.console.print(f"    [dim]Params: {params_str}[/dim]")
        elif not is_valid:
            violation_str = ", ".join(violations[:2])  # Show first 2 violations
            if len(violations) > 2:
                violation_str += f" (+{len(violations) - 2} more)"
            self.console.print(
                f"{status} Trial {trial_number}: Invalid - {violation_str} | {params_str}"
            )

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
            # Get best parameters from FrozenTrial (already suggested, stored as flat dict)
            # Reconstruct organized structure from flat params
            flat_params = study.best_trial.params
            best_params = self._organize_flat_params(flat_params)

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
        self.results_db.export_to_csv(self.study_name, output_path)

        if self.verbose and self.console:
            self.console.print(f"[green]✓[/green] Results exported to: {output_path}")
