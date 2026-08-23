# Pre-Flight Checklist

- [ ] Are short-term (20d) and long-term (100d) rolling correlation matrices computed across a synchronized, timezone-aligned multi-asset universe?
- [ ] Have stale/flat (zero-variance) series been screened out rather than imputed?
- [ ] Is the K-normalized Frobenius distance ($\|C_1 - C_2\|_F / K$) understood to scale with universe size — thresholds not transferred across K?
- [ ] Are regime thresholds calibrated to the rolling-$D_F$ empirical distribution for this exact universe and window lengths (not left at 0.30/0.60 defaults)?
- [ ] Is a whipsaw guard (N consecutive days above threshold) in place before automated de-leveraging?
- [ ] Is the average off-diagonal correlation trigger ($\bar{\rho} \ge 0.65$) also calibrated, and checked independently of $D_F$?
- [ ] Are leverage multipliers (0.50/0.80) validated against the mandate's whipsaw tolerance and integrated with exposure/VaR limits?
- [ ] Has an EWMA cross-check (RiskMetrics $\lambda = 0.94$) been considered to smooth window-split noise?
- [ ] Is Pearson-based tail blindness acknowledged (EVT/copula methods needed for extreme co-movement)?
