# Workflows for CDS Basics in Algo Context

1. **Hazard Rate Estimation**:
   - Credit triangle: $\lambda = \frac{s_{par}}{1 - R}$ with $R = 0.40$.
   - Decision point: if the name trades near/above ~1000 bps, the flat-hazard
     assumption degrades (distressed regime, points-upfront quoting) — do not
     strip a hazard rate without specialist treatment.
2. **Survival & Default Probabilities**:
   - $S(T) = e^{-\lambda T}$; $PD(T) = 1 - S(T)$.
3. **Risky PV01 (RPV01)**:
   - $RPV01 = \frac{1 - e^{-(r + \lambda) T}}{r + \lambda}$; the continuous-annuity
     approximation of the survival-discounted premium leg.
   - Limit: $RPV01 \to T$ as $(r + \lambda) \to 0$.
4. **Indicative Upfront Payment**:
   - $\text{Upfront} = \text{Notional} \times RPV01 \times (s_{par} - s_{coupon})$.
   - Buyer pays when $s_{par} > s_{coupon}$; seller pays the reverse; zero at par.
   - Decision point: for settlement-exact figures, run the ISDA CDS Standard
     Model (cdsmodel.com) — quarterly IMM premiums and Act/360 accrual will
     move the number vs. this continuous approximation.
5. **Credit Tier Classification**:
   - Boundaries 150 / 1000 bps (informal desk conventions; parameterisable in
     the constructor). 500 bps = standard HY coupon, hence NOT distressed.
6. **Cross-Asset Signal Execution**:
   - $z = (s_{last} - \bar{s})/\sigma_s$ over the spread history (population std).
   - If $z > \text{threshold} \implies$ `SHORT_EQUITY_LONG_CDS`;
     $z < -\text{threshold} \implies$ `LONG_EQUITY_SHORT_CDS`; else NEUTRAL.
   - Decision point: $\sigma_s = 0$ (flat history) → NEUTRAL; fewer than two
     observations → data error, not a signal.
