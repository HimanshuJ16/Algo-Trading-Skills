# Standards for Explainable Boosting Machines (EBM)

| Metric | Engineering Standard |
|---|---|
| SR 11-7 Model Governance | EBM signals MUST provide exact, un-approximated feature attributions. |
| Additive Exactness | Model score MUST satisfy $\beta_0 + \sum f_i(x_i) + \sum f_{jk}(x_j, x_k) \equiv \hat{Y}$. |
| Monotonicity Constraints | Financial domain shape curves MUST be audited for monotonic sanity. |