"""Entry point for running runners as modules.

This module allows flexible invocation of different runner types:
    python -m naut_hedgegrid.runners paper
    python -m naut_hedgegrid.runners live
    python -m naut_hedgegrid.runners backtest

If no runner type is specified, defaults to backtest.
"""

import sys

if __name__ == "__main__":
    # Determine which runner to use based on first argument
    if len(sys.argv) > 1 and sys.argv[1] in ["paper", "live", "backtest"]:
        runner_type = sys.argv.pop(1)
    else:
        runner_type = "backtest"  # Default

    # Import and run the appropriate runner
    if runner_type == "paper":
        from naut_hedgegrid.runners.run_paper import app
    elif runner_type == "live":
        from naut_hedgegrid.runners.run_live import app
    else:
        from naut_hedgegrid.runners.run_backtest import app

    app()
