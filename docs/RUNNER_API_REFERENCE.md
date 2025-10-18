# Runner API Reference

## Overview

This document provides a quick reference for the helper functions available in the paper and live trading runners.

## Helper Functions

### load_strategy_config()

**Location**: `run_paper.py`, `run_live.py`

**Signature**:
```python
def load_strategy_config(
    strategy_config_path: Path,
    hedge_grid_cfg: HedgeGridConfig,
    venue_cfg: VenueConfig,
) -> HedgeGridV1Config
```

**Purpose**: Convert HedgeGridConfig (from YAML) to HedgeGridV1Config (for Nautilus TradingNode)

**Parameters**:
- `strategy_config_path`: Path to hedge_grid_v1.yaml file
- `hedge_grid_cfg`: Loaded HedgeGridConfig object
- `venue_cfg`: Loaded VenueConfig object

**Returns**: HedgeGridV1Config ready for TradingNode

**Example**:
```python
strat_cfg = load_strategy_config(
    strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    hedge_grid_cfg=hedge_grid_cfg,
    venue_cfg=venue_cfg,
)
```

---

### create_bar_type()

**Location**: `run_paper.py`, `run_live.py`

**Signature**:
```python
def create_bar_type(instrument_id_str: str) -> BarType
```

**Purpose**: Programmatically construct BarType for 1-minute bars

**Parameters**:
- `instrument_id_str`: Instrument ID (e.g., "BTCUSDT-PERP.BINANCE")

**Returns**: BarType configured for 1-minute LAST bars

**Example**:
```python
bar_type = create_bar_type("BTCUSDT-PERP.BINANCE")
# bar_type: BTCUSDT-PERP.BINANCE-1-MINUTE-LAST
```

**Note**: Currently not used in main flow (string-based approach working), but available for troubleshooting.

---

### create_data_client_config()

**Location**: `run_paper.py`, `run_live.py`

**Signature**:
```python
def create_data_client_config(
    instrument_id: str,
    venue_cfg: VenueConfig,
    api_key: str | None,
    api_secret: str | None,
) -> BinanceDataClientConfig
```

**Purpose**: Configure Binance data client with efficient instrument subscription

**Parameters**:
- `instrument_id`: Full instrument ID (e.g., "BTCUSDT-PERP.BINANCE")
- `venue_cfg`: VenueConfig with API settings
- `api_key`: Binance API key (optional for public data)
- `api_secret`: Binance API secret (optional for public data)

**Returns**: BinanceDataClientConfig with instrument filtering

**Example**:
```python
data_client_config = create_data_client_config(
    instrument_id="BTCUSDT-PERP.BINANCE",
    venue_cfg=venue_cfg,
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
)
```

**Key Feature**: Sets `load_all=False` and filters to only specified symbol

---

### create_exec_client_config()

**Location**: `run_live.py` only

**Signature**:
```python
def create_exec_client_config(
    venue_cfg: VenueConfig,
    api_key: str | None,
    api_secret: str | None,
) -> BinanceExecClientConfig
```

**Purpose**: Configure Binance execution client for live trading

**Parameters**:
- `venue_cfg`: VenueConfig with API settings
- `api_key`: Binance API key (required)
- `api_secret`: Binance API secret (required)

**Returns**: BinanceExecClientConfig configured for hedge mode

**Example**:
```python
exec_client_config = create_exec_client_config(
    venue_cfg=venue_cfg,
    api_key=os.getenv("BINANCE_API_KEY"),
    api_secret=os.getenv("BINANCE_API_SECRET"),
)
```

**Critical Setting**: `use_reduce_only=False` for hedge mode support

---

### create_node_config()

**Location**: `run_paper.py`, `run_live.py`

**Signature**:

**Paper Trading**:
```python
def create_node_config(
    strategy_config: HedgeGridV1Config,
    data_client_config: BinanceDataClientConfig,
    is_live: bool = False,
) -> TradingNodeConfig
```

**Live Trading**:
```python
def create_node_config(
    strategy_config: HedgeGridV1Config,
    data_client_config: BinanceDataClientConfig,
    exec_client_config: BinanceExecClientConfig | None = None,
    is_live: bool = False,
) -> TradingNodeConfig
```

**Purpose**: Create TradingNodeConfig for paper or live trading

**Parameters**:
- `strategy_config`: HedgeGridV1Config
- `data_client_config`: BinanceDataClientConfig
- `exec_client_config`: BinanceExecClientConfig (live only)
- `is_live`: True for live, False for paper

**Returns**: TradingNodeConfig

**Example (Paper)**:
```python
node_config = create_node_config(
    strategy_config=strat_cfg,
    data_client_config=data_client_config,
    is_live=False,
)
```

**Example (Live)**:
```python
node_config = create_node_config(
    strategy_config=strat_cfg,
    data_client_config=data_client_config,
    exec_client_config=exec_client_config,
    is_live=True,
)
```

---

## Complete Integration Example

### Paper Trading

```python
from pathlib import Path
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfigLoader
from naut_hedgegrid.runners.run_paper import (
    load_strategy_config,
    create_data_client_config,
    create_node_config,
)
from nautilus_trader.live.node import TradingNode

# Load configurations
hedge_grid_cfg = HedgeGridConfigLoader.load("configs/strategies/hedge_grid_v1.yaml")
venue_cfg = VenueConfigLoader.load("configs/venues/binance_futures.yaml")

# Create strategy config
strat_cfg = load_strategy_config(
    strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    hedge_grid_cfg=hedge_grid_cfg,
    venue_cfg=venue_cfg,
)

# Create data client config
data_client_config = create_data_client_config(
    instrument_id=hedge_grid_cfg.strategy.instrument_id,
    venue_cfg=venue_cfg,
    api_key=None,
    api_secret=None,
)

# Create node config
node_config = create_node_config(
    strategy_config=strat_cfg,
    data_client_config=data_client_config,
    is_live=False,
)

# Create and start node
node = TradingNode(config=node_config)
node.build()
node.start()
```

### Live Trading

```python
import os
from pathlib import Path
from naut_hedgegrid.config.strategy import HedgeGridConfigLoader
from naut_hedgegrid.config.venue import VenueConfigLoader
from naut_hedgegrid.runners.run_live import (
    load_strategy_config,
    create_data_client_config,
    create_exec_client_config,
    create_node_config,
)
from nautilus_trader.live.node import TradingNode

# Load configurations
hedge_grid_cfg = HedgeGridConfigLoader.load("configs/strategies/hedge_grid_v1.yaml")
venue_cfg = VenueConfigLoader.load("configs/venues/binance_futures.yaml")

# Get API keys
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

# Create strategy config
strat_cfg = load_strategy_config(
    strategy_config_path=Path("configs/strategies/hedge_grid_v1.yaml"),
    hedge_grid_cfg=hedge_grid_cfg,
    venue_cfg=venue_cfg,
)

# Create data client config
data_client_config = create_data_client_config(
    instrument_id=hedge_grid_cfg.strategy.instrument_id,
    venue_cfg=venue_cfg,
    api_key=api_key,
    api_secret=api_secret,
)

# Create exec client config
exec_client_config = create_exec_client_config(
    venue_cfg=venue_cfg,
    api_key=api_key,
    api_secret=api_secret,
)

# Create node config
node_config = create_node_config(
    strategy_config=strat_cfg,
    data_client_config=data_client_config,
    exec_client_config=exec_client_config,
    is_live=True,
)

# Create and start node
node = TradingNode(config=node_config)
node.build()
node.start()
```

---

## Configuration Flow Diagram

```
hedge_grid_v1.yaml → HedgeGridConfigLoader → HedgeGridConfig
                                                     │
                                                     ▼
                                          load_strategy_config()
                                                     │
                                                     ▼
                                             HedgeGridV1Config
                                                     │
                     ┌───────────────────────────────┼───────────────────────────────┐
                     │                               │                               │
                     ▼                               ▼                               ▼
         create_data_client_config()   create_exec_client_config()    (instrument_id, bar_type)
                     │                               │                               │
                     ▼                               ▼                               ▼
         BinanceDataClientConfig         BinanceExecClientConfig                  (metadata)
                     │                               │                               │
                     └───────────────────────────────┼───────────────────────────────┘
                                                     │
                                                     ▼
                                          create_node_config()
                                                     │
                                                     ▼
                                             TradingNodeConfig
                                                     │
                                                     ▼
                                                TradingNode
```

---

**Document Version**: 1.0
**Last Updated**: 2025-10-14
