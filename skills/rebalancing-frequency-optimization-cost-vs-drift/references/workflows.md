# Workflows for Rebalancing Frequency Optimization Cost vs Drift

## 1. Snapshot validation (before any measurement)

- Reject non-finite `target_weight`, `current_weight`, `asset_value_usd`, `fee_rate_bps`,
  `estimated_slippage_bps`. **This must run first**: `NaN` compares `False` against every
  threshold, so an unvalidated `NaN` drift would report "no rebalance" with `NaN` costs.
- Reject duplicate symbols — drift and trades would be double-counted.
- Require $\sum_i w_{i,\text{target}} = 1$ and $\sum_i w_{i,\text{current}} = 1$ within
  `weight_tolerance`; otherwise buys and sells cannot net to zero.
- Require total portfolio value finite and $> 0$. Never substitute a fallback value.
- Require each `current_weight` to equal `asset_value_usd / total_value`. Drift is
  measured from the weight but order size comes from the value.

Any violation raises `ValueError`. `enabled=False` returns `ENGINE_DISABLED` and an empty
asset list returns `NO_ASSETS`; neither is an error.

## 2. Drift measurement

- Signed drift $d_i = w_{i,\text{current}} - w_{i,\text{target}}$.
- Band metric $\max_i |d_i|$; penalty input $\sum_i d_i^2$ (accumulated with `math.fsum`).

## 3. Destination and candidate trade construction

- No destination set $\Rightarrow$ shrink $k = 0$, every leg trades fully to target.
- Destination $b$ set $\Rightarrow k = \min(1, b / \max_i|d_i|)$, applied **uniformly** to
  every leg. The largest-drift asset lands on $b$; residual drifts still sum to zero, so
  post-trade weights still sum to one. Per-leg independent clamping does not preserve
  this for three or more assets.
- Traded weight $= |d_i|(1-k)$; direction is `SELL` when $d_i > 0`, else `BUY`.
- Drop any leg below `min_leg_trade_pct` or `min_leg_trade_usd`; record it in
  `suppressed_legs`. Suppression leaves a cash imbalance the caller must settle.

## 4. Cost evaluation

- $\text{TxCost} = \sum_i \text{traded}_i \cdot V \cdot (\text{FeeBps}_i + \text{SlipBps}_i)/10^4$,
  priced on the **post-shrink, post-filter** traded weight.
- $\text{DriftCost} = \lambda \cdot H \cdot \sum_i d_i^2 \cdot V$, where $H$ is
  `drift_horizon_periods`. $\lambda$ and $H$ must be expressed in the same time unit as
  the interval between calls.
- $\text{NetBenefit} = \text{DriftCost} - \text{TxCost}$.

## 5. Trigger evaluation

1. $\max_i|d_i| \ge$ `max_drift_threshold_pct` $\rightarrow$
   `REBALANCE_TRIGGERED_MAX_DRIFT` (risk mandate; economics ignored).
2. Else NetBenefit $> 0$ **and** $\max_i|d_i| \ge$ `min_trade_threshold_pct`
   $\rightarrow$ `REBALANCE_TRIGGERED_NET_BENEFIT`.
3. Else `NO_REBALANCE_WITHIN_BAND`; the trade list is cleared but the costed alternative
   remains in the cost fields.
4. If (1) or (2) fired but the trade set is empty, downgrade to
   `REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES` with `rebalance_recommended=False`. If the band
   was breached, log at `WARNING` and escalate — the mandate breach is unremediated.

## 6. Audit report

Emit `RebalanceOptimizationReport` with portfolio value, drift cost, transaction cost,
net benefit, max single drift, `destination_drift_pct`, `suppressed_legs`, status, and
the human-readable `audit_notes` line that is also logged at `INFO`.
