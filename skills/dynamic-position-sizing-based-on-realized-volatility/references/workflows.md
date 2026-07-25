# Deep Workflow Reference — dynamic-position-sizing-based-on-realized-volatility

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Estimate Realized Volatility**:
   - EWMA RiskMetrics ($\lambda=0.94$): $\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1 - \lambda) r_t^2$.
   - Annualize volatility: $\sigma_{\text{ann}} = \sqrt{252} \times \sigma_t$.
2. **Compute Volatility Scalar**:
   $$\text{Scalar} = \frac{\sigma_{\text{target}}}{\max(\sigma_{\text{floor}}, \sigma_{\text{ann}})}$$
3. **Apply Bounding Limits**:
   $$\text{BoundedScalar} = \text{clip}(\text{Scalar}, \text{MinScalar}, \text{MaxScalar})$$
4. **Scale Base Capital Allocation**:
   $$\text{AdjustedCapital} = \text{BaseCapital} \times \text{BoundedScalar}$$

## Production Implementation Reference

- Reference code: `scripts/realized_vol_sizer.py` (`RealizedVolPositionSizer`, `VolatilityTargetingResult`).
- Automated unit tests: `scripts/test_realized_vol_sizer.py`.
