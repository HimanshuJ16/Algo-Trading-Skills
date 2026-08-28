# Workflows for Strategy Capacity Estimation Before Scaling Capital

## 1. Parameter definition and unit contract

Inputs: gross annual return, annual volatility, one-way daily turnover %, ADV in the
same currency as AUM, daily return volatility, half-spread in bps, participation cap in
percent, minimum net Sharpe, and the risk-free rate.

Three unit mistakes account for most wrong answers, and none of them look wrong in the
output:

- **Turnover convention.** `daily_turnover_pct` is **one-way** notional as a fraction of
  AUM, paired with a **half**-spread charged once. A two-way turnover figure passed here
  doubles both the spread and the impact base.
- **Excess vs total return.** `risk_free_rate_pct` defaults to `0.0`, which is correct
  only if `annual_gross_return_pct` is already an excess return. Otherwise the risk-free
  rate is silently credited to the strategy.
- **Percent vs fraction.** Returns, volatilities, and turnover are fractions; the
  participation cap is a percentage. `max_participation_rate_pct=0.05` means five
  hundredths of one percent of ADV, not 5%.

## 2. Impact model calibration — do this before trusting any number

$I(Q) = Y \cdot \sigma_{\text{daily}} \cdot \sqrt{Q/V}$.

$Y$ (`impact_gamma`) is the single largest lever on the result. Fit it against your own
realized slippage: regress observed implementation shortfall on
$\sigma_{\text{daily}}\sqrt{Q/V}$ over your own typical order sizes and instruments. See
`execution-cost-model-recalibration-cadence` for the recalibration cadence.

Until that is done, run the estimate at both ends of the empirical $0.5$–$1.0$ range and
treat the **lower** capacity as the working number. Because impact drag is linear in $Y$,
the two runs typically differ by a factor that dwarfs every other modelling choice here.

## 3. Grid construction

The AUM grid is $\text{step}, 2\times\text{step}, \dots$ up to `max_search_aum_usd`,
derived from the loop index rather than accumulated, so a step that is not exactly
representable in binary cannot drift or silently drop the final point.

Choose the step for the precision you need: capacity is reported only to
`capacity_resolution_usd`. A $\$1\text{M}$ step over a $\$200\text{M}$ range costs 200
evaluations and is instant; there is rarely a reason to go coarser.

Two grids are rejected outright rather than producing a plausible-looking report: a
non-positive step (the pre-audit loop never terminated on a negative one) and a step
wider than the search range (an empty curve whose zero capacity is indistinguishable
from a genuine zero-capacity finding).

## 4. Decay curve generation

At each grid point the engine computes daily notional, ADV participation, annual spread
cost, annual impact cost, net PnL, net return, and net Sharpe. Curve fields are rounded
for display; the gates are evaluated against `net_sharpe_ratio_exact` and
`adv_participation_pct_exact`, so a point can display `1.0` and still be flagged as
breaching.

## 5. Capacity limit determination

Capacity is the largest AUM with an **unbroken feasible run beneath it**: the scan stops
at the first breach and a later non-breaching point cannot resurrect it. Under this cost
model the curve is monotone, so the distinction rarely bites — but defining capacity as
"the last feasible point anywhere on the grid" would let a non-monotone curve report a
capacity above a region the strategy cannot actually trade through.

Interpret the limiting factor:

- **`ADV_PARTICIPATION_LIMIT`** — liquidity binds first. Levers: lower turnover, widen the
  universe, lengthen the holding period, or extend execution across days
  (`multi-day-execution-schedules-for-very-large-orders`).
- **`MIN_SHARPE_BREACH`** — impact drag binds first. Levers: reduce turnover, improve
  execution, or accept a lower Sharpe gate as a deliberate policy decision.
- **`BELOW_MIN_SHARPE_AT_ALL_SIZES`** — the strategy never clears the gate, even at one
  grid step. This is a strategy-quality verdict, not a capacity limit; no amount of
  liquidity relieves it. Route to `strategy-lifecycle-retirement-criteria`.
- **`SEARCH_RANGE_EXHAUSTED`** — nothing breached inside the range. The reported capacity
  is the search ceiling you chose, not a measured limit. Widen `max_search_aum_usd` and
  re-run before using the number for anything.

## 6. Reading the optimum

`optimal_sharpe_capacity_aum_usd` maximises net **dollar** PnL among feasible points only.
`unconstrained_max_pnl_aum_usd` maximises it over the whole grid, gates ignored, and is a
diagnostic — it exists to make the gap visible.

That gap is the point. With the reference parameters, net dollar PnL is still rising at
$\$100\text{M}$ against a capacity of $\$25\text{M}$: more capital keeps making more money
right up to and past the participation cap, because the dollar-PnL curve knows nothing
about whether the fills are achievable. Allocating to the unconstrained peak is the single
most expensive way to misread this report.

## 7. Sanity checks before acting on the number

- Is `impact_gamma` fitted, or still the default?
- Is `search_range_exhausted` false?
- Does `max_capacity_aum_usd` exceed `capacity_resolution_usd` by enough that the grid
  precision is immaterial to the decision?
- Is the aggregate ADV honest, or does the book hold a tail of names far less liquid than
  the blended figure? Cross-check with `liquidity-adjusted-position-sizing`.
- Is the gross return genuinely size-invariant over the range being considered? It never
  fully is — the reported capacity is an upper bound, so deploy beneath it and ramp in
  stages (`incremental-capital-deployment-for-new-strategies`).
