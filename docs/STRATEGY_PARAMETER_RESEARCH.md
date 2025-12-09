# HedgeGridV1 Strategy Parameter Research

> Pre-optimization research to establish logical parameter bounds and starting values.

## Executive Summary

The HedgeGridV1 strategy is a hedge-mode grid trading system that adapts to market regimes. This document analyzes all 17 tunable parameters and provides research-backed recommendations for optimization.

**Key Finding:** The current configuration has an unfavorable risk/reward ratio (2.5:1) requiring 71% win rate to break even. This should be addressed before optimization.

---

## Table of Contents

1. [Current Configuration Analysis](#current-configuration-analysis)
2. [Parameter Deep Dive](#parameter-deep-dive)
3. [Research Findings](#research-findings)
4. [Recommended Starting Configuration](#recommended-starting-configuration)
5. [Optimization Priority](#optimization-priority)
6. [Parameter Interaction Matrix](#parameter-interaction-matrix)

---

## Current Configuration Analysis

| Parameter | Current Value | Assessment |
|-----------|---------------|------------|
| grid_step_bps | 50 | Good - within optimal 30-100 bps range |
| grid_levels_long | 7 | Adequate - 5-10 is optimal |
| grid_levels_short | 7 | Adequate - 5-10 is optimal |
| base_qty | 0.002 BTC | Conservative (~$180 at $90k) |
| qty_scale | 1.05 | Safe (5% geometric growth) |
| tp_steps | 2 | **PROBLEM** - Only 1% profit target |
| sl_steps | 5 | **PROBLEM** - 2.5% risk = unfavorable R/R |
| ema_fast | 12 | Classic MACD period |
| ema_slow | 26 | Classic MACD period |
| adx_len | 14 | Standard period |
| atr_len | 14 | Standard period |
| hysteresis_bps | 50 | **ISSUE** - Equals grid_step, may cause oscillation |
| counter_levels | 5 | Reasonable throttling |
| counter_qty_scale | 0.5 | Good 50% reduction |
| recenter_trigger_bps | 300 | At upper bound (6x grid_step) |
| funding_max_cost_bps | 10 | Conservative threshold |
| max_position_pct | 0.8 | Aggressive for $10k account |

### Critical Issue: Risk/Reward Ratio

**Current Setup Math:**
- Take Profit: 2 steps × 50 bps = **1.0% profit**
- Stop Loss: 5 steps × 50 bps = **2.5% risk**
- Risk/Reward Ratio: **2.5:1** (unfavorable)
- Break-even Win Rate: 2.5 / (1 + 2.5) = **71%**

This is unsustainable. Grid strategies typically achieve 50-65% win rates.

---

## Parameter Deep Dive

### 1. Grid Parameters

#### grid_step_bps (Grid Spacing)
- **Purpose:** Vertical distance between price levels
- **Current:** 50 bps (0.5%)
- **Research Range:** 30-100 bps for BTC
- **Optimization Range:** 40-80 bps

**Impact:**
- Smaller values → More fills, higher fee costs, more order churn
- Larger values → Fewer fills, wider gaps, less capital efficiency

**Formula:** `price_step = mid_price × (grid_step_bps / 10000)`

#### grid_levels_long / grid_levels_short
- **Purpose:** Number of rungs below/above mid price
- **Current:** 7 each
- **Research Range:** 5-10 for $10k account
- **Optimization Range:** 5-8

**Coverage Calculation:**
```
Price coverage = levels × grid_step_bps
Example: 7 levels × 50 bps = 350 bps (3.5%) per side
```

#### base_qty (Initial Order Size)
- **Purpose:** Quantity for closest level to mid
- **Current:** 0.002 BTC (~$180 at $90k)
- **Research Range:** 0.001-0.005 BTC (log scale)
- **Optimization Range:** 0.0015-0.003 BTC

#### qty_scale (Geometric Scaling)
- **Purpose:** Multiplier per level (further = larger)
- **Current:** 1.05 (5% increase)
- **Research Range:** 1.0-1.1 (max 10%)
- **Optimization Range:** 1.0-1.08

**Total Inventory Formula:**
```python
if qty_scale == 1.0:
    total_qty = base_qty × levels
else:
    total_qty = base_qty × (1 - qty_scale^levels) / (1 - qty_scale)

# Example: 7 levels, 0.002 BTC, 1.05 scale
total = 0.002 × (1 - 1.05^7) / (1 - 1.05) = 0.0163 BTC per side
```

---

### 2. Exit Parameters

#### tp_steps (Take Profit Distance)
- **Purpose:** Grid steps above entry for profit target
- **Current:** 2 steps
- **Research Recommendation:** 3-5 steps for better R/R
- **Optimization Range:** 2-5

#### sl_steps (Stop Loss Distance)
- **Purpose:** Grid steps from entry for loss limit
- **Current:** 5 steps
- **Research Recommendation:** 3-8 steps
- **Optimization Range:** 3-8

**Recommended Combinations:**

| TP Steps | SL Steps | R/R Ratio | Break-even Win Rate |
|----------|----------|-----------|---------------------|
| 2 | 4 | 1:2 | 33% |
| 3 | 5 | 1:1.67 | 37% |
| 3 | 4 | 1:1.33 | 43% |
| 4 | 6 | 1:1.5 | 40% |
| 4 | 5 | 1:1.25 | 44% |

**Constraint:** `tp_steps ≤ sl_steps × 3` (enforced in optimizer)

---

### 3. Regime Detection Parameters

#### ema_fast / ema_slow
- **Purpose:** Trend direction via crossover
- **Current:** 12 / 26 (MACD standard)
- **Research:** Fast should be 40-50% of slow
- **Optimization Range:** Fast 8-15, Slow 25-45

**EMA Spread Calculation:**
```
spread_bps = (fast_ema - slow_ema) / slow_ema × 10000
```

#### adx_len (Trend Strength)
- **Purpose:** ADX indicator period (0-100 scale)
- **Current:** 14
- **Threshold:** 20 (below = SIDEWAYS regime)
- **Optimization Range:** 10-20

**ADX Interpretation:**
- ADX < 20: Weak/no trend → Force SIDEWAYS
- ADX 20-40: Trending → Allow UP/DOWN
- ADX > 40: Strong trend → Regime-following optimal

#### atr_len (Volatility)
- **Purpose:** Average True Range period
- **Current:** 14
- **Usage:** Risk management, exit sizing
- **Optimization Range:** 10-20

#### hysteresis_bps (Anti-Flip Band)
- **Purpose:** Prevents rapid regime switching
- **Current:** 50 bps
- **Issue:** Should NOT equal grid_step_bps
- **Optimization Range:** 20-60 bps

**Logic:**
```
If currently UP:
  Stay UP until spread < -hysteresis_bps
  Then switch to DOWN
```

---

### 4. Policy Parameters

#### counter_levels (Throttled Side Depth)
- **Purpose:** How many rungs on counter-trend side
- **Current:** 5
- **Impact:** 0 = disable counter, grid_levels = no throttling
- **Optimization Range:** 3-7

#### counter_qty_scale (Quantity Reduction)
- **Purpose:** Scale down counter-trend quantities
- **Current:** 0.5 (50% reduction)
- **Research:** 30-50% reduction optimal
- **Optimization Range:** 0.4-0.6

**Throttling Effect by Regime:**

| Regime | Trend Side | Counter Side |
|--------|-----------|--------------|
| UP | SHORT (full) | LONG (throttled) |
| DOWN | LONG (full) | SHORT (throttled) |
| SIDEWAYS | Both full | Both full |

---

### 5. Rebalance Parameters

#### recenter_trigger_bps
- **Purpose:** When to move grid around new mid price
- **Current:** 300 bps (3%)
- **Research:** Should be 4-6x grid_step_bps
- **Optimization Range:** 150-350 bps

**Relationship Rule:**
```
recenter_trigger_bps ≈ 4 × grid_step_bps

Examples:
- grid_step=50 → recenter=200 (4x)
- grid_step=75 → recenter=300 (4x)
```

---

### 6. Funding Parameters

#### funding_max_cost_bps
- **Purpose:** Max acceptable funding cost threshold
- **Current:** 10 bps
- **Research:** Binance averages 1 bps, spikes to 10+ bps
- **Optimization Range:** 8-25 bps

**Funding Impact:**
- Positive rate: Longs pay shorts
- Negative rate: Shorts pay longs
- Guard scales down paying side as funding time approaches

---

### 7. Position Parameters

#### max_position_pct
- **Purpose:** Fraction of account for positions
- **Current:** 0.8 (80%)
- **Research:** Conservative 50-75% recommended
- **Optimization Range:** 0.5-0.8

---

## Research Findings

### Grid Trading Best Practices (2024-2025)

1. **Optimal Spacing:** 30-100 bps (0.3-1%) for BTC
2. **Grid Levels:** 5-10 for accounts under $50k
3. **Risk/Reward:** Target 1:2 or better (33% break-even)
4. **Trend Awareness:** Critical - grid trading without trend detection has ~0 expected value after fees
5. **Funding Impact:** Can dominate PnL in extended operations
6. **Kelly Criterion:** Half-Kelly (50%) preferred for position sizing

### Dynamic Grid Trading (DGT) Research

Recent research (2024) shows Dynamic Grid Trading that:
- Incorporates trend detection
- Adjusts grid levels dynamically
- Outperforms buy-and-hold with lower drawdown

**HedgeGridV1 already implements these via:**
- RegimeDetector (EMA/ADX/ATR)
- PlacementPolicy (throttling)
- OrderDiff (dynamic updates)

---

## Recommended Starting Configuration

Based on research, here is the optimized starting configuration:

```yaml
# Optimized HedgeGridV1 Configuration
# Research-based starting point for optimization

strategy:
  name: hedge_grid_v1
  instrument_id: BTCUSDT-PERP.BINANCE

grid:
  grid_step_bps: 50.0        # Keep at 0.5% - proven spacing
  grid_levels_long: 6        # Reduce from 7 (lower inventory)
  grid_levels_short: 6       # Symmetric
  base_qty: 0.002            # Keep conservative
  qty_scale: 1.05            # Keep at 5%

exit:
  tp_steps: 3                # INCREASED from 2 (better R/R)
  sl_steps: 5                # Keep at 5 → 1.67:1 R/R

rebalance:
  recenter_trigger_bps: 200.0  # REDUCED from 300 (more responsive)
  max_inventory_quote: 10000.0

execution:
  maker_only: true
  use_post_only_retries: true
  retry_attempts: 3
  retry_delay_ms: 100

funding:
  funding_window_minutes: 480
  funding_max_cost_bps: 12.0   # INCREASED from 10

regime:
  adx_len: 14
  ema_fast: 10               # REDUCED from 12 (faster response)
  ema_slow: 30               # INCREASED from 26 (wider spread)
  atr_len: 14
  hysteresis_bps: 35.0       # REDUCED from 50 (decouple from grid_step)

position:
  max_position_size: 0.1
  max_position_pct: 0.75     # REDUCED from 0.8 (more conservative)
  max_leverage_used: 5.0
  emergency_liquidation_buffer: 0.15

policy:
  strategy: throttled-counter
  counter_levels: 4          # REDUCED from 5
  counter_qty_scale: 0.5
```

### Key Changes from Current Config

| Parameter | Current | Recommended | Rationale |
|-----------|---------|-------------|-----------|
| tp_steps | 2 | **3** | Better R/R ratio (1.67:1 vs 2.5:1) |
| ema_fast | 12 | **10** | Faster trend response |
| ema_slow | 26 | **30** | Wider EMA spread for clearer signals |
| hysteresis_bps | 50 | **35** | Decouple from grid_step |
| recenter_trigger_bps | 300 | **200** | More responsive recentering |
| counter_levels | 5 | **4** | Tighter throttling |
| max_position_pct | 0.8 | **0.75** | More conservative |
| grid_levels | 7 | **6** | Lower inventory requirement |

---

## Optimization Priority

Based on parameter sensitivity analysis:

| Priority | Parameters | Impact | Notes |
|----------|-----------|--------|-------|
| **1** | tp_steps, sl_steps | Very High | Directly determines profitability |
| **2** | grid_step_bps | High | Affects fill frequency and fees |
| **3** | ema_fast, ema_slow | High | Determines regime accuracy |
| **4** | counter_qty_scale | Medium | Controls inventory bias |
| **5** | base_qty, qty_scale | Medium | Position sizing |
| **6** | hysteresis_bps | Medium | Prevents regime flip-flop |
| **7** | grid_levels | Low | Bounded by account size |
| **8** | adx_len, atr_len | Low | Standard values work well |

### Recommended Optimization Phases

**Phase 1: Exit Parameters (Critical)**
- Focus on tp_steps and sl_steps
- Target R/R ratio of 1:1.5 to 1:2
- 50-100 trials

**Phase 2: Grid + Regime (High Impact)**
- grid_step_bps with EMA parameters
- Ensure hysteresis < grid_step
- 100-200 trials

**Phase 3: Full Space (Fine-tuning)**
- All 17 parameters
- Use Phase 1-2 best values as starting point
- 200+ trials

---

## Parameter Interaction Matrix

### Critical Interactions

| Pair | Relationship | Rule |
|------|-------------|------|
| grid_step ↔ recenter_trigger | Scaling | recenter ≈ 4× grid_step |
| ema_fast ↔ ema_slow | Ordering | fast < slow (enforced) |
| ema_fast ↔ hysteresis | Stability | Wide EMA spread needs smaller hysteresis |
| tp_steps ↔ sl_steps | R/R Ratio | tp ≤ sl × 3 (enforced) |
| grid_levels ↔ base_qty | Inventory | Must fit max_inventory_quote |
| counter_levels ↔ grid_levels | Throttling | counter < grid_levels to have effect |

### Warmup Requirements

```
Minimum bars = max(ema_slow, adx_len × 2, atr_len)

Default config:  max(26, 28, 14) = 28 bars
Recommended:     max(30, 28, 14) = 30 bars
With buffer:     50-70 bars recommended
```

---

## Appendix: Inventory Calculations

### Total Exposure at $90k BTC

**Current Config (7 levels, 0.002 BTC, 1.05 scale):**
```
Per side: 0.002 × (1 - 1.05^7) / (1 - 1.05) = 0.0163 BTC
Both sides: 0.0326 BTC
Value: 0.0326 × $90,000 = $2,934
```

**Recommended Config (6 levels, 0.002 BTC, 1.05 scale):**
```
Per side: 0.002 × (1 - 1.05^6) / (1 - 1.05) = 0.0136 BTC
Both sides: 0.0272 BTC
Value: 0.0272 × $90,000 = $2,448
```

**With Throttling (UP regime, counter=4, scale=0.5):**
```
LONG (throttled): 4 levels × 0.5 scale ≈ 0.0045 BTC
SHORT (full): 6 levels = 0.0136 BTC
Total: 0.0181 BTC = $1,629
```

---

*Document generated: 2024-12-08*
*Version: 1.0*
