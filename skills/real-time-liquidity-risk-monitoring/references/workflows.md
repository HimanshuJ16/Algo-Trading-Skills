# Workflows for Real-Time Liquidity Risk Monitoring

## 0. Prepare the observation set

1. **Net the book by symbol.** One observation per symbol; aggregate lots first. A
   duplicate symbol double-counts notional and liquidation cost, and the engine raises
   rather than silently summing it.
2. **Check units.** Spreads in price units (not basis points); ADV and depth in
   shares/contracts; prices and VaR in one currency. The validator can only check signs
   and finiteness — a basis-point spread passed as a price unit produces a
   plausible-looking wrong answer.
3. **Choose the ADV deliberately.** Trailing calm-market ADV answers "how long would
   this have taken last month". If the report is meant to inform stress governance,
   supply the ADV expected *in stress*.
4. **Compute the baseline VaR separately.** `baseline_var_usd` is a required argument —
   your own mid-price VaR at a stated confidence level, horizon, and currency.

## 1. Days to Liquidate (DTL)

$$\text{DTL}_i = \frac{|\text{Position}_i|}{\text{MaxParticipationPct} \cdot \text{ADV}_i}$$

- Magnitude, not signed size: covering a short consumes liquidity like selling a long,
  so portfolio notional is **gross**.
- Breach when $\text{DTL}_i \ge$ `max_dtl_threshold_days` (inclusive).
- `max_days_to_liquidate` on the report is the worst symbol in the book, not an average
  — a portfolio is only as liquid as the position that traps it.

## 2. Spread spike and L2 depth audit

$$\text{SpreadRatio}_i = \frac{\text{Spread}_i}{\text{NormalSpread}_i}, \qquad
\text{DepthDrop}_i = 1 - \frac{\text{Depth}_i}{\text{NormalDepth}_i}$$

- Each symbol is compared against **its own** baseline, so an intrinsically wide-spread
  small-cap is not flagged merely for being a small-cap.
- Both are inclusive thresholds.
- `depth_drop_pct` is floored at $0.0$: depth above normal reports no drop rather than a
  negative one.
- Non-finite or non-positive inputs raise. Do not pre-clamp them on the way in — that
  reintroduces the fabricated-reading failure this validation exists to prevent.

## 3. Cost of Liquidity and L-VaR

$$\text{COL}_i = \tfrac{1}{2}\,\text{Notional}_i\left(\frac{\text{Spread}_i}{\text{Price}_i} + k \cdot \text{DTL}_i\right),
\qquad \text{L-VaR} = \text{BaselineVaR} + \sum_i \text{COL}_i$$

- First term: BDSS (1999) Eq. 4 with tail scaler $a = 0$ — snapshot half-spread, no
  spread-volatility term, so it is a mean-condition and not a tail estimate.
- Second term: an uncalibrated linear-in-horizon impact proxy. Review
  `impact_cost_usd` against `spread_cost_usd` before quoting any L-VaR: if the impact
  half dominates, the number is mostly a function of $k$, which no source prescribes.
- Costs sum with no diversification benefit, and are added straight onto the mid-price
  VaR. BDSS justify the additive form by assuming extreme returns and extreme spreads
  coincide.

## 4. Report and escalate

- `status` is one of `LIQUIDITY_HEALTHY`, `LIQUIDITY_RISK_ALERT`, `NO_POSITIONS`.
  `NO_POSITIONS` means the book was empty — it is not a pass.
- Per-symbol breaches are logged at `WARNING` with the metric that fired; the portfolio
  summary is logged at `WARNING` on alert and `INFO` otherwise. Configure a handler in
  the host application — the module never configures logging itself.
- Route the report to enforcement (kill switch, unwind scheduler, position-limit
  system). This engine never blocks, resizes, or cancels.
