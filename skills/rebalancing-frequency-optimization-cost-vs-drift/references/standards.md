# Standards — rebalancing-frequency-optimization-cost-vs-drift

## Configuration defaults (calibrate before use)

**These are library defaults, not industry standards.** No regulator or standards body
prescribes a rebalancing tolerance band, a drift penalty, or a destination point. The
right values depend on the mandate's tracking-error budget, the sleeves' liquidity, the
account's tax status, and how often the decision is evaluated. Calibrate each and record
the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `drift_penalty_lambda` ($\lambda$) | $1.0$ | Converts squared active weight into a currency cost **per evaluation period**. Not dimensionless: it bundles risk aversion and asset variance. A placeholder — calibrate it. |
| `drift_horizon_periods` ($H$) | $1.0$ | Number of periods the drift is assumed to persist before the next decision. Makes the per-period penalty comparable to the one-shot trade cost. |
| `max_drift_threshold_pct` | $0.05$ | Tolerance-band half-width. Breach is **inclusive** ($\ge$): exactly $5.00\%$ triggers. |
| `min_trade_threshold_pct` | $0.01$ | Largest drift must reach this before the net-benefit rule may fire. |
| `destination_drift_pct` | `None` | Post-trade drift of the largest-drift asset. `None` = rebalance fully to target. Must be $<$ `max_drift_threshold_pct`. |
| `min_leg_trade_pct` | $0.0005$ | Legs trading under $5$ bps of portfolio weight are dropped. |
| `min_leg_trade_usd` | $0.0$ | Notional floor per leg. Disabled by default. |
| `weight_tolerance` | $10^{-6}$ | Tolerance for the weight-sum and value/weight consistency checks. |

## Decision rules as implemented

| Rule | Condition | Status |
|---|---|---|
| Risk mandate | $\max_i \lvert d_i \rvert \ge$ `max_drift_threshold_pct` | `REBALANCE_TRIGGERED_MAX_DRIFT` |
| Economic | $\text{NetBenefit} > 0$ **and** $\max_i \lvert d_i \rvert \ge$ `min_trade_threshold_pct` | `REBALANCE_TRIGGERED_NET_BENEFIT` |
| Neither | otherwise | `NO_REBALANCE_WITHIN_BAND` (no trades emitted) |
| Trigger fired, trade set empty | drift already inside the destination, or all legs below the minimum sizes | `REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES`, `rebalance_recommended=False` |

`REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES` exists so a trigger can never be reported
alongside an empty trade list. When it arises from a **band breach** it is a mandate
breach the engine cannot remediate, is logged at `WARNING`, and requires escalation —
collapsing it into `NO_REBALANCE_WITHIN_BAND` would hide a live risk-limit breach.

$$\text{DriftCost} = \lambda H \sum_i d_i^2 \cdot V \qquad
\text{TxCost} = \sum_i \text{traded}_i \cdot V \cdot \frac{\text{FeeBps}_i + \text{SlipBps}_i}{10^4}$$

with $d_i = w_{i,\text{current}} - w_{i,\text{target}}$, and $\text{traded}_i =
\lvert d_i \rvert (1 - k)$ where $k = \min(1, b / \max_i \lvert d_i \rvert)$ is the
uniform destination shrink ($k = 0$ when no destination is set).

`TxCost` is priced on $\text{traded}_i$ — the trade actually emitted — not on
$\lvert d_i \rvert$. The `min_trade_threshold_pct` gate exists because a quadratic
penalty against a linear cost yields a positive net benefit for arbitrarily small
drifts; without the gate the engine would trade at every evaluation.

## Evidence for trading to the band boundary

Source: **Leland, Hayne E. (1999), "Optimal Portfolio Management with Transactions Costs
and Capital Gains Taxes,"** Research Program in Finance Working Paper Series, Institute
for Business and Economic Research, UC Berkeley
([eScholarship](https://escholarship.org/uc/item/0fw6k0hm) ·
[SSRN](https://doi.org/10.2139/ssrn.206871)).

| Claim | Status |
|---|---|
| Under proportional transaction costs the optimal policy is a **no-trade region** around the target proportions; no trading occurs while inside it | Verified in the paper's abstract |
| When outside the region, trade to move the ratio to the **region's boundary** — not to the target | Verified in the paper's abstract |
| Transaction costs may be asymmetric between buys and sells and may include a capital-gains-tax component | Verified in the paper's abstract |
| The asymptotic half-width of the no-trade region scales as the **cube root** of transaction costs | Standard result in the small-cost literature; **not implemented here** |

**What this engine does not claim.** It does not compute Leland's multi-asset no-trade
region, which depends on the covariance matrix and per-asset cost asymmetry and is solved
numerically. `max_drift_threshold_pct` is a user-supplied band, not a derived one, and
the destination shrink is a budget-preserving uniform generalisation of "trade to the
boundary" — not Leland's optimal boundary. Earlier versions of this skill described
themselves as implementing "the Leland No-Trade Band model"; that attribution was
inaccurate and has been removed.

## Evidence for a destination point short of target

Source: **Zhang, Y., Ahluwalia, H., Daga, A., and Zi, Y. (December 2024), "The
rebalancing edge: Optimizing target-date fund rebalancing through threshold-based
strategies,"** Vanguard Research
([PDF](https://corporate.vanguard.com/content/dam/corp/research/pdf/the_rebalancing_edge_optimizing_target_date_fund_rebalancing_through_threshold_based_strategies.pdf)).

| Claim | Status |
|---|---|
| On a threshold breach, allocations are rebalanced to a **destination point, which may be the target itself or a point between the target and the threshold** | Verified, p. 4 |
| Worked "200/175" example: threshold $200$ bps, destination $175$ bps; a $60/40$ book breaching $62\%$ equity is rebalanced to $61.75\%$ | Verified, p. 4 — reproduced exactly in this skill's test suite |
| "Selecting a destination closer to the threshold can help reduce the size of rebalancing trades and lower the associated transaction costs" | Verified, p. 4 |
| Over 10 years, 200/175 averaged $0.05\%$ transaction cost across $92$ events (trade size $0.88\%$) versus monthly $0.22\%$ / $120$ events / $2.00\%$ and quarterly $0.18\%$ / $40$ events / $3.68\%$ | Verified, Figure 6 |
| Expected 1-year maximum allocation deviation: $198$ bps for 200/175 versus $241$ bps monthly and $333$ bps quarterly | Verified, Figure 9 |

**Applicability.** These figures are Vanguard's simulation results for a global $60/40$
target-date book with no cash flows and no futures overlay. They are evidence that a
destination short of target reduces turnover; they are **not** a recommendation of
$200/175$ for an arbitrary portfolio, and they are not reproduced by this engine, which
performs no simulation.

## Known limitations

- $\sum_i d_i^2$ is **not tracking error**. It equals tracking-error variance only under
  $\Sigma = \sigma^2 I$. Correlation is not modelled.
- **Single-period, no forecast.** No return, volatility, or drift forecast enters the
  decision.
- **Proportional costs only.** No fixed per-order cost, tiered commission, borrow cost,
  or non-linear market impact.
- **No taxes.** Leland's model includes capital gains; this one does not.
- **Trades are gross of cost and assume no external cash flow.** Buys and sells net to
  zero, so fees and slippage are unfunded, and suppressing a leg leaves a residual
  imbalance the caller must settle in cash.
