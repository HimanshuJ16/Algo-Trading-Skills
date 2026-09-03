# Deep Workflow Reference — transaction-cost-analysis-tca-integration

This file holds the full technical procedure referenced by `SKILL.md`.

Notation: $d = +1$ for a buy, $-1$ for a sell; all bps figures are relative to
$P_{\text{decision}}$ and **positive means adverse**.

## Full Procedure

### 1. Validate the trade record before computing anything

Reject rather than repair:

- `adv <= 0` — an ADV floor (the pre-1.1 `max(1.0, adv)`) turns missing liquidity
  data into a fabricated 100% participation rate that is indistinguishable from a
  genuine full-ADV order.
- Non-positive or non-finite prices — a NaN otherwise propagates to
  `net_tca_return_pct`, where it compares `False` against every threshold and
  quietly fails viability without anyone learning the costs were never computed.
- `action` outside `{BUY, SELL}` — a `"SEL"` typo must not be read as a sell and
  invert the sign of every price-based cost.
- `filled_size` outside `[0, order_size]`. The comparison is float-tolerant
  (`FILL_SIZE_REL_TOL = 1e-9`): a fill accumulated from child fills lands a few
  ulps either side of the parent size (three 0.1-unit fills of a 0.3-unit order
  sum to `0.30000000000000004`), and a strict comparison would either reject a
  complete fill as an over-fill or leave a 5e-17-unit "unfilled remainder" that
  demands a $P_{\text{end}}$ to price. A genuine over-fill is orders of magnitude
  outside the tolerance and still raises.

### 2. Realized implementation shortfall (ex-post)

$$IS_{\text{realized}} = d\cdot\frac{P_{\text{fill}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10^4 + \text{commission}_{\text{bps}}$$

This is the Perold (1988) measurement and the number that reflects what execution
actually cost. A completely unfilled order pays no execution cost and no
commission; its cost is opportunity cost (step 4).

### 3. Modelled estimate (ex-ante)

$$IS_{\text{est}} = \underbrace{d\frac{P_{\text{arr}} - P_{\text{dec}}}{P_{\text{dec}}}10^4}_{\text{delay}} + \underbrace{\frac{0.5\,\text{Spread}}{P_{\text{dec}}}10^4}_{\text{half-spread}} + \underbrace{\gamma\sqrt{\phi}}_{\text{impact}} + \text{commission}_{\text{bps}}$$

with participation $\phi = \text{OrderSize}/\text{ADV}$.

Check $\phi \in [10^{-5}, 0.1]$. Outside that band the engine sets
`participation_out_of_model_range` and logs a warning. The impact number is still
computed — it is an extrapolation, not a clamp — but must not be trusted. Clamping
$\phi$ at 1.0, as pre-1.1 did, made a 100×-ADV order price identically to a
1×-ADV order at exactly $\gamma$.

The half-spread is charged unconditionally, so the estimate over-charges passive
fills. For maker flow, read `realized_shortfall_bps`.

### 4. Opportunity cost on the unfilled remainder

Perold's IS covers the whole order. With fill ratio $f = \text{filled}/\text{order}$:

$$IS_{\text{total}} = f\cdot IS_{\text{exec}} + (1-f)\cdot d\frac{P_{\text{end}} - P_{\text{dec}}}{P_{\text{dec}}}10^4 + f\cdot\text{commission}_{\text{bps}}$$

$P_{\text{end}}$ is the terminal benchmark price at which the remainder would have
to be chased. When shares go unfilled and no $P_{\text{end}}$ is supplied,
`opportunity_cost_bps` and `total_implementation_shortfall_bps` are `None`, never
`0.0`, and the portfolio summary increments `unpriced_opportunity_trades`.
Substituting zero would report the most expensive outcome in the framework — a
miss in a market that ran away — as free, inverting the ranking of execution
venues.

### 5. Calibrate the slippage model from the residual

$$r_i = IS_{\text{exec},i} - \text{delay}_i - \text{spread}_i, \qquad \hat{\gamma} = \arg\min_\gamma \sum_i (r_i - \gamma\sqrt{\phi_i})^2 = \frac{\sum_i r_i\sqrt{\phi_i}}{\sum_i \phi_i}$$

Only filled orders carry impact information; unfilled ones are excluded. A
negative $\hat{\gamma}$ is clamped to `0.0` with a warning: impact cannot be a
credit, and a negative residual means something other than impact dominates
(passive fills earning the spread, or favourable drift).

Refit **per instrument liquidity bucket and per volatility regime**. Because
$\gamma$ folds $Y\sigma$ into a single bps constant, a single global fit is only
valid for the instrument and regime it was fitted on.

Feed $\hat{\gamma}$ back into the backtest's fill-price adjustment, re-run, and
confirm the notional-weighted `model_error_bps` has shrunk toward zero. A model
error that stays large and positive means the backtest is still systematically
cheaper than reality.

### 6. Aggregate to a portfolio return drag

$$\text{cost}_i^{\text{ccy}} = \frac{IS_{\text{realized},i}}{10^4}\left(\text{filled}_i \cdot P_{\text{dec},i}\right) + \text{opportunity cost}_i^{\text{ccy}}$$

$$\text{drag}\% = \frac{\sum_i \text{cost}_i^{\text{ccy}}}{\text{capital base}}\times 100, \qquad \text{net}\% = \text{gross}\% - \text{drag}\%$$

`capital_base` is mandatory. The pre-1.1 formula summed per-trade *bps* and read
the result as a percentage, so 1,000 one-share trades costing about four cents in
total subtracted 35 percentage points from the strategy return and flagged it
non-viable, while a single order for half a day's volume barely registered.

Two weightings are reported:

- `avg_implementation_shortfall_bps` — equal-weighted. Useful for spotting a
  systematically bad venue; misleading as a portfolio cost measure.
- `notional_weighted_shortfall_bps` — ties to `total_cost_currency` and is the
  figure viability is judged on.

If `unpriced_opportunity_trades > 0`, `net_tca_return_pct` is an optimistic bound.

## Breaking changes in 1.1.0

| Change | Migration |
|---|---|
| `evaluate_portfolio_tca` now requires `capital_base` | Pass the capital that produced `gross_return_pct`. |
| `TCATradeBreakdown.total_shortfall_bps` is now a read-only property aliasing `estimated_shortfall_bps` | Read `estimated_shortfall_bps` or `realized_shortfall_bps` explicitly. Construction by keyword no longer accepts it. |
| `avg_implementation_shortfall_bps` is now computed from *realized* shortfall | Read `avg_estimated_shortfall_bps` for the previous basis. |
| Non-positive ADV, non-positive prices, NaN/Inf, and unknown actions now raise | Filter or repair upstream; these previously produced silent, plausible-looking numbers. |
| Participation is no longer clamped to 1.0 | Check `participation_out_of_model_range`. |

## Production Implementation Reference

- Reference code: `scripts/tca_integrator.py`
  (`TCABacktestIntegrator`, `TCATradeBreakdown`, `TCAPortfolioSummary`,
  `suggest_market_impact_gamma`).
- Automated unit tests: `scripts/test_tca_integrator.py`.
