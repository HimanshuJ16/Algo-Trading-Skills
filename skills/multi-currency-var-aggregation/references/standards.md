# Standards for Multi-Currency VaR Aggregation

| Metric | Engineering Standard |
|---|---|
| Return Synthesis | Asset and FX returns MUST be compounded ($R_{\text{base}} = (1+R_{\text{native}})(1+R_{\text{FX}}) - 1$). |
| Confidence Levels | 95% ($Z=1.645$) and 99% ($Z=2.326$) confidence levels MUST be supported. |
| Tail Risk Metric | Expected Shortfall (CVaR) MUST be reported alongside VaR. |