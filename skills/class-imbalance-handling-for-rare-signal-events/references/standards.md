# Standards for Class Imbalance

| Metric | Engineering Standard |
|---|---|
| No Leakage | Balancing algorithms MUST NEVER be applied to validation or out-of-sample test datasets. The test set must reflect the raw, imbalanced reality of the market. |
| Cost Function Scaling | When calculating class weights dynamically, the formula $Weight = \frac{Total\_Samples}{Num\_Classes \times Class\_Samples}$ ensures that the total sum of weights across all samples remains consistent with an unweighted dataset. |
| Reproducibility | Any randomized resampling algorithm (like random undersampling) MUST accept a random seed to ensure deterministic model training pipelines. |
