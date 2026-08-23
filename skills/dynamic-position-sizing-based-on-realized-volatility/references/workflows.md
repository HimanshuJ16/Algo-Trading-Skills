# Deep Workflow Reference — dynamic-position-sizing-based-on-realized-volatility

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Prepare and validate the return series**:
   - Slice the history so it ends at the last **completed** observation before the bar
     being sized. RiskMetrics Eq. 5.37 forecasts period $t$ from $r_{t-1}$; including the
     current bar leaks its outcome into the position size.
   - Reject non-finite values. A single `NaN` collapses a naive variance to zero, which
     the volatility floor then converts into the maximum leverage scalar — the largest
     position from the worst data.
   - Confirm the sampling frequency matches `annualization_factor` (observations per
     year: 252 daily, $252 \times 78$ for 5-minute bars). It cannot be inferred from the data.

1. **Estimate Realized Volatility**:
   - EWMA RiskMetrics: $\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1 - \lambda) r_t^2$, seeded with
     $r_0^2$ and using raw squared returns (zero-mean convention, RiskMetrics Sec. 5.3.1.2).
   - Rolling: mean-subtracted sample standard deviation with the $(n-1)$ correction.
     This embeds a *different* mean assumption from the EWMA; the two will not agree.
   - Require at least $K = \ln(\text{tolerance}) / \ln(\lambda)$ observations (Eq. 5.26) —
     74 at $\lambda = 0.94$ and a 1% tolerance. Below $K$ the seed still carries more than
     the tolerance in weight, so the "estimate" is largely an artifact of $r_0$.
   - Annualize: $\sigma_{\text{ann}} = \sqrt{F} \times \sigma_t$.
   - Report the estimate **unfloored**: the floor belongs to sizing, not to measurement.

2. **Compute Volatility Scalar**:
   $$\text{Scalar} = \frac{\sigma_{\text{target}}}{\max(\sigma_{\text{floor}}, \sigma_{\text{ann}})}$$
   - Flag when the floor binds. A floor-bound size is a leverage brake on an abnormally
     quiet series, not a volatility reading.

3. **Apply Bounding Limits**:
   $$\text{BoundedScalar} = \text{clip}(\text{Scalar}, \text{MinScalar}, \text{MaxScalar})$$
   - Note which constraint actually binds: with $\sigma_{\text{target}} = 15\%$ and
     $\sigma_{\text{floor}} = 5\%$ the raw scalar cannot exceed $3.0$, so a `MaxScalar` of
     $2.0$ is the effective ceiling and the floor never binds first.

4. **Scale Base Capital Allocation**:
   $$\text{AdjustedCapital} = \text{BaseCapital} \times \text{BoundedScalar}$$
   - Convert to shares by **flooring**, never rounding up: rounding up places a position
     above the risk budget the whole procedure exists to enforce. Lot- and tick-size
     rounding is out of scope — see `minimum-fill-size-and-lot-rounding-logic`.

## Production Implementation Reference

- Reference code: `scripts/realized_vol_sizer.py`
  (`RealizedVolPositionSizer`, `VolatilityTargetingResult`, `required_ewma_observations`).
- Automated unit tests: `scripts/test_realized_vol_sizer.py`, including reproduction of
  RiskMetrics Table 5.7 and analytically derived volatility expectations.
