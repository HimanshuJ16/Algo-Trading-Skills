# Standards for Portfolio Construction with Transaction Cost Awareness

## Engine parameters

| Metric | Engineering Standard |
|---|---|
| Buffer Band Threshold | Default $2.0\%$ weight shift, **inclusive** ($|\Delta w| \le$ threshold is suppressed), compared with a relative tolerance of $10^{-9}$ so binary representation error cannot push an exactly-at-threshold move onto the trading path. This is a configuration default, not an industry rule — the economically correct band widens with cost and narrows with alpha decay, so calibrate it. |
| Transaction Cost Model | $\text{TC} = \left(c_{\text{comm}} + \frac{c_{\text{spread,bps}}}{10^4}\right)|\Delta w| + c_{\text{impact}}(\Delta w)^2$, charged on the **executed** delta, in units of portfolio value. |
| Turnover Convention | `total_turnover` is **two-way** (L1 norm, $\sum|\Delta w|$); `one_way_turnover` is half of it. A full liquidation-and-replacement of a long book is $2.0$ two-way. |
| Max Turnover Limit | Default $50.0\%$ **two-way**. **Advisory only** — the engine sets `turnover_limit_breached` and returns the plan unclamped. The caller must gate execution. |
| Weight Units | Fractions, not percentages. $|w| > 10$ is rejected as a percent-vs-fraction input error. |
| Budget Identity | Partial suppression breaks it. `net_weight_change` and `is_self_financing` report the funding leg; they are not enforced, because cash holdings and leverage are legitimate. |

## Market impact functional form

The quadratic impact term is a **tractability choice, not an empirical law**, and this
distinction matters for how far the cost estimate can be trusted.

- A transaction cost quadratic in trade size is exactly equivalent to a **linear**
  price impact function. Gârleanu and Pedersen (2013) adopt it because it is the form
  under which dynamic portfolio choice with predictable returns admits closed-form
  solutions; the tractability, not the realism, is the motivation.
- Measured metaorder impact is **concave** in size — the "square-root law". Fitted
  exponents cluster around $0.5$, with Almgren et al. (2005) and Kyle and Obizhaeva
  (2016) reporting values closer to $0.6$; the literature range is roughly $0.4$–$0.7$.
- Consequence: relative to observed impact, a quadratic model **understates** the cost
  of small trades and **overstates** the cost of large ones. Cost comparisons across
  very different trade sizes are the least reliable output of this engine.
- `impact_coeff` therefore has **no transferable default**. Fit it to your own realized
  slippage over your own typical trade sizes. The shipped `0.5` is a deliberately
  conspicuous placeholder: it charges $50$ bps of portfolio value on a $10\%$ trade,
  dwarfing the $\sim 1$ bp proportional term, so an uncalibrated run is obvious.

## No-trade band policy

Under **purely proportional** transaction costs the optimal policy is a no-trade region,
and when the region is breached the optimal correction is to trade back to the **nearest
boundary** — not to the target (Constantinides 1986; Davis and Norman 1990). Trading to
target pays proportional cost on weight change that buys no utility.

Caveats that bound this result:

- It assumes proportional costs only. With a material **fixed** cost per trade the
  optimum moves to a point strictly *inside* the band. This engine models no fixed cost,
  so it implements neither that refinement.
- The engine's default (`trade_to_band_edge=False`) snaps to target, preserving the
  original behaviour. `trade_to_band_edge=True` implements the boundary policy.

## Sources

| Claim | Source |
|---|---|
| No-trade region; rebalance to the nearest boundary under proportional costs | Constantinides, G. (1986), *Capital Market Equilibrium with Transaction Costs*, JPE; Davis, M. and Norman, A. (1990), *Portfolio Selection with Transaction Costs*, Mathematics of Operations Research |
| Quadratic cost ⇔ linear price impact, adopted for tractability | Gârleanu, N. and Pedersen, L. H. (2013), *Dynamic Trading with Predictable Returns and Transaction Costs*, Journal of Finance |
| Concave (square-root) empirical impact, exponent ≈ 0.5–0.6 | Almgren, R., Thum, C., Hauptmann, E. and Li, H. (2005), *Direct Estimation of Equity Market Impact*, Risk; Kyle, A. and Obizhaeva, A. (2016), *Market Microstructure Invariance*, Econometrica |
| Two-way turnover as the L1 norm of the weight change | Standard portfolio-analytics convention; the halved figure is the one-way measure used in fund turnover disclosure |
