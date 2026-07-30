# Standards for Futures Contract Roll Automation

| Metric | Engineering Standard |
|---|---|
| Volume Crossover Rule | Roll MUST trigger when next-month volume exceeds front-month ($V_{\text{next}} > V_{\text{front}}$). |
| Expiration Threshold | Futures MUST be rolled at least 5 business days prior to Last Trading Day / First Notice. |
| Order Type | Roll execution MUST use atomic exchange Calendar Spread orders (`SP`) to eliminate legging risk. |