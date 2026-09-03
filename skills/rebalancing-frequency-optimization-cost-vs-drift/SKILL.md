---
name: rebalancing-frequency-optimization-cost-vs-drift
description: >-
  Use when a portfolio has drifted from target weights and you must decide whether to
  trade at all and how far back, weighing a quadratic drift penalty against the cost of
  the trade actually placed.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: rebalancing-optimization, cost-vs-drift, no-trade-band, tolerance-band, transaction-costs, portfolio-governance
  brokers_frameworks: "Threshold Rebalancing (No-Trade Band); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a multi-asset portfolio drifts away from its target weights and you
must decide **whether to trade at all** and **how far back to trade**. Rebalancing on a
fixed calendar incurs turnover the drift never justified; ignoring drift lets weights
wander outside the risk mandate. The engine evaluates a tolerance ("no-trade") band
alongside an explicit cost/benefit comparison and, optionally, rebalances only back to
the band's boundary rather than all the way to target.

Typical callers: a daily or intraday portfolio governance job, a strategy-allocation
rebalancer, or a pre-trade gate that decides whether a scheduled rebalance should run.

## When NOT to Use

- **As a tracking-error model.** The penalty is $\sum_i d_i^2$, the squared L2 norm of
  the active-weight vector. That equals tracking-error variance only when the covariance
  matrix is $\sigma^2 I$ — uncorrelated assets of equal variance. Two correlated equity
  sleeves 2% apart are penalised identically to a 2% stock/bond gap, which is wrong. Use
  a covariance-aware measure when correlation matters.
- **On a taxable account, on this output alone.** Capital-gains realisation is not
  modelled and routinely exceeds the modelled cost saving. See
  `cross-strategy-tax-lot-optimization`.
- **When fixed or non-linear costs dominate.** Costs here are strictly proportional to
  notional. Per-order minimums, tiered commissions, borrow costs, and square-root market
  impact are absent, so both very small and very large orders are mispriced.
- **As an execution algorithm.** It emits notional deltas, not orders. Slicing, venue
  choice, and order lifecycle belong to `execution-algo-twap-vwap-slicing` and
  `portfolio-construction-with-transaction-cost-awareness`.
- **Without calibrating $\lambda$ and the horizon.** See the first pitfall below — the
  defaults are placeholders, not recommendations.

## Prerequisites

- A **consistent** portfolio snapshot: `symbol`, `target_weight`, `current_weight`,
  `asset_value_usd`, `fee_rate_bps`, `estimated_slippage_bps`. Target weights must sum to
  $1$, current weights must sum to $1$, and each `current_weight` must equal
  `asset_value_usd / total_value`. The engine raises `ValueError` rather than trading on
  a snapshot that violates any of these.
- A calibrated `drift_penalty_lambda` (library default $1.0$ — a placeholder, not a
  recommendation) and `drift_horizon_periods` expressing how long the drift persists
  before the next evaluation.
- A `max_drift_threshold_pct` band half-width (default $0.05$) and
  `min_trade_threshold_pct` gate (default $0.01$).

## Workflow

1. **Validate the snapshot before measuring anything.**
   - Reject non-finite values *first*. Every comparison against `NaN` is `False`, so an
     unvalidated `NaN` drift passes every threshold test and reports "no rebalance" with
     `NaN` costs — a silent wrong answer, not a loud one.
   - Reject duplicate symbols, weight sums off $1$, and any `current_weight` that
     disagrees with `asset_value_usd / total_value`. Drift is measured from the weight
     but trades are sized from the value; an inconsistent snapshot yields wrongly sized
     orders that still look plausible.

2. **Compute drift and the candidate trade set.**
   - Signed drift $d_i = w_{i,\text{current}} - w_{i,\text{target}}$; band metric is
     $\max_i |d_i|$.
   - **Decision point — pick the destination.** With `destination_drift_pct = None` every
     leg trades fully to target. With a destination $b$ set, apply the *uniform* shrink
     $k = b / \max_i|d_i|$ so the largest-drift asset lands exactly on $b$ and residual
     drifts still sum to zero. Clamping each leg to $b$ independently would break that
     identity for three or more assets and produce post-trade weights that do not sum
     to one.
   - **Decision point — drop negligible legs.** A leg below `min_leg_trade_pct` or
     `min_leg_trade_usd` is suppressed and named in `suppressed_legs`.

3. **Price the trade you would actually place.**
   $$\text{TxCost} = \sum_i \text{traded}_i \cdot V \cdot \frac{\text{FeeBps}_i + \text{SlipBps}_i}{10000}$$
   Price the *post-shrink, post-filter* traded weight, not the raw drift. Pricing raw
   drift overstates the cost of a partial rebalance and biases the decision toward
   inaction.

4. **Compare against the drift penalty.**
   $$\text{DriftCost} = \lambda \cdot H \cdot \sum_i d_i^2 \cdot V, \qquad \text{NetBenefit} = \text{DriftCost} - \text{TxCost}$$
   $H$ is `drift_horizon_periods`. Both sides must refer to the same time span — see the
   first pitfall.

5. **Apply the two trigger rules, in order.**
   - $\max_i|d_i| \ge$ `max_drift_threshold_pct` $\Rightarrow$
     `REBALANCE_TRIGGERED_MAX_DRIFT`. The band edge is **inclusive**: exactly 5.00%
     breaches a 5% band. This is the risk-mandate rule and it ignores the economics.
   - Otherwise, NetBenefit $> 0$ **and** $\max_i|d_i| \ge$ `min_trade_threshold_pct`
     $\Rightarrow$ `REBALANCE_TRIGGERED_NET_BENEFIT`. The second condition is what stops
     micro-drift churn: with a quadratic penalty and linear costs, net benefit is
     positive for arbitrarily small drifts, so without the gate the engine would trade
     every evaluation.
   - Otherwise `NO_REBALANCE_WITHIN_BAND`, and no trades are emitted. The costed trade
     set still appears in the cost fields as the evaluated alternative.
   - **Decision point — a trigger with nothing to trade.** If a rule fires but every leg
     was filtered out (drift already inside the destination, or all legs below the
     minimum sizes), the status becomes `REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES` and
     `rebalance_recommended` is `False` — there is nothing to place. When the *band* was
     breached this is a live mandate breach the engine cannot remediate: it is logged at
     `WARNING` and must be escalated, not read as "flat".

6. **Audit.** Return `RebalanceOptimizationReport`, carrying `destination_drift_pct` and
   `suppressed_legs` so the reviewer can see how far back the book was traded and what
   was deliberately left alone.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing a per-period penalty against a one-shot cost.** `DriftCost` is a *flow* —
  the risk cost of carrying the drift for a period. `TxCost` is a *stock* — paid once.
  If $\lambda$ is annual and you evaluate daily without setting
  `drift_horizon_periods = 1/252`, the drift penalty is overstated ~252× and the engine
  trades every single day. Shortening the evaluation interval must not, by itself, make
  rebalancing look more attractive. Set both $\lambda$ and $H$ deliberately, in the same
  time unit.
- **Trusting `current_weight` and `asset_value_usd` separately.** Drift comes from the
  weight; the order size comes from the value. If a stale snapshot lets them disagree,
  the engine happily sizes an order against the wrong denominator. Cross-check them —
  the engine raises rather than proceeding.
- **A suppressed leg leaves an unfunded cash imbalance.** Dropping a negligible leg means
  sells no longer net against buys. In the shipped 5-sleeve example a $\$1{,}503$ gap
  remains. That is deliberate — it is cheaper than the dropped order — but the caller
  must settle it in cash, not assume the trade list is self-financing.
- **Transaction costs themselves are unfunded.** Buy and sell notionals net to zero by
  construction, so nothing is reserved for fees and slippage. Reserve separately.
- **Over-rebalancing on micro-drifts.** Rebalancing for sub-1% shifts, where fees and
  slippage exceed any risk reduction. Governed here by `min_trade_threshold_pct` and the
  per-leg minimums — do not set all three to zero.
- **Rebalancing all the way to target by reflex.** Under proportional costs the optimal
  policy trades back to the *boundary* of the no-trade region, not to the target
  (Leland 1999). Set `destination_drift_pct` when turnover matters; in the worked 200/175
  case it cuts the trade notional 8×.
- **Whipsaw in trending markets.** Restoring target weights in a strong trend
  systematically cuts winners. The band is what limits how often this happens; widening
  it is a deliberate risk/turnover trade, not a bug fix.
- **Treating the defaults as standards.** No regulator or standards body prescribes a
  rebalancing band, a $\lambda$, or a destination. Every number here is a house choice.

## Verification

- Instantiate `RebalancingFrequencyOptimizerEngine(Config(drift_penalty_lambda=100.0,
  max_drift_threshold_pct=0.05))`. Feed $V=\$1{,}000{,}000$, target $50/50$, current
  $60/40$ at $5+5$ bps: verify `REBALANCE_TRIGGERED_MAX_DRIFT`, drift cost
  $=100 \cdot (0.1^2 + 0.1^2) \cdot 10^6 = \$2{,}000{,}000$, transaction cost
  $= 2 \cdot \$100{,}000 \cdot 10/10000 = \$200$, net benefit $\$1{,}999{,}800$, and a
  $\$100{,}000$ SELL/BUY pair. Feed $50.5/49.5$ ($0.5\%$ drift): verify
  `NO_REBALANCE_WITHIN_BAND` and zero emitted trades.
- Reproduce Vanguard's published 200/175 example: threshold $0.02$, destination $0.0175$,
  a $60/40$ book at $62/38$. Verify equity is rebalanced to exactly $61.75\%$, each leg
  trades $25$ bps ($\$2{,}500$), and total cost is $\$5$ versus $\$40$ for a full
  rebalance to target.
- Negative checks: a `NaN` weight, an infinite asset value, a zero portfolio value,
  weights not summing to one, a `current_weight` disagreeing with `asset_value_usd`, a
  duplicate symbol, a negative fee, and a `destination_drift_pct` $\ge$
  `max_drift_threshold_pct` must each raise `ValueError`.
- Verify the uniform shrink keeps residual drifts summing to zero on a three-asset book
  with asymmetric drifts.
- Run `python -m unittest discover -s skills/rebalancing-frequency-optimization-cost-vs-drift/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `portfolio-construction-with-transaction-cost-awareness`
- `capital-efficiency-across-cross-margined-strategies`
- `transaction-cost-analysis-tca-integration`
- `correlation-aware-exposure-limits`
- `cross-strategy-tax-lot-optimization`
