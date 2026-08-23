# Pre-Flight Checklist

- [ ] Is the recovery rate set to the convention-correct value (40% senior unsecured default; subordinated lower), and within [0, 1)?
- [ ] Is the standard coupon correct for the market (100/500 bps SNAC for North American corporates; other grids elsewhere)?
- [ ] Is hazard rate derived via the credit-triangle approximation acknowledged as flat-hazard (no curve stripping)?
- [ ] Are cumulative default probability ($PD$) and survival probability ($S$) evaluated for the target maturity?
- [ ] Is Risky PV01 ($RPV01$) computed with the survival-discounted continuous integral, including the $r + \lambda \to 0$ limit?
- [ ] Is the upfront figure labelled indicative — with settlement-exact conversion reserved for the ISDA CDS Standard Model (quarterly IMM, Act/360)?
- [ ] Is the credit tier classified with the 150 / 1000 bps desk conventions (not 500, which is the standard HY coupon)?
- [ ] For cross-asset signals: does the spread history have genuine dispersion and enough observations for a meaningful z-score?
- [ ] For names near or above ~1000 bps: has the distressed regime (points-upfront quoting, degraded credit-triangle assumptions) been considered before using these metrics?
