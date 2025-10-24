"""Parameter optimization framework for HedgeGridV1 strategy.

This package provides a comprehensive Bayesian optimization framework
using Optuna for finding optimal strategy parameters through backtesting.
"""

from naut_hedgegrid.optimization.constraints import ConstraintsValidator
from naut_hedgegrid.optimization.objective import MultiObjectiveFunction
from naut_hedgegrid.optimization.optimizer import StrategyOptimizer
from naut_hedgegrid.optimization.parallel_runner import ParallelBacktestRunner
from naut_hedgegrid.optimization.param_space import ParameterSpace
from naut_hedgegrid.optimization.results_db import OptimizationResultsDB

__all__ = [
    "ConstraintsValidator",
    "MultiObjectiveFunction",
    "OptimizationResultsDB",
    "ParallelBacktestRunner",
    "ParameterSpace",
    "StrategyOptimizer",
]
