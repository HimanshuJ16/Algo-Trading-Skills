# Standards for Quanto Options and Cross-Currency Derivative Structures

| Metric | Engineering Standard |
|---|---|
| Quanto Drift Adjustment | $r_{\text{quanto}} = r_f - q - \rho \cdot \sigma_S \cdot \sigma_X$ MUST be used for foreign asset drift. |
| Discount Rate | Domestic risk-free rate $r_d$ MUST be used for discounting the payoff. |
| Correlation Sensitivity | FX correlation sensitivity ($\partial V / \partial \rho$) MUST be computed and tracked. |
