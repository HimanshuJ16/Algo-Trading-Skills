# Standards for Class Imbalance

| Metric | Engineering Standard |
|---|---|
| No Leakage | Balancing algorithms MUST NEVER be applied to validation or out-of-sample test datasets. The test set must reflect the raw, imbalanced reality of the market. |
| Cost Function Scaling | When calculating class weights dynamically, the formula $Weight_c = \frac{Total\_Samples}{Num\_Classes \times Class\_Samples_c}$ makes every class contribute equally while the sum of per-sample weights stays equal to `n_samples` ($\sum_c count_c \cdot \frac{n}{k \cdot count_c} = n$), so the effective dataset size is unchanged. |
| Parameter Shape Matching | Class weights MUST be handed to the estimator in the shape it documents. scikit-learn estimators take a `{label: weight}` mapping via `class_weight`; XGBoost takes a single scalar `scale_pos_weight`. The two encode different quantities and are not interchangeable. |
| Probability Recalibration | Any model trained on a rebalanced prior produces probabilities that are NOT calibrated to the real event rate. Before a probability is used as a probability (expected value, Kelly sizing, absolute thresholds), the prior shift MUST be undone or the model MUST be recalibrated on data with the original class distribution. |
| Evaluation Metric | Model selection under heavy imbalance MUST use precision/recall-based metrics (PR-AUC, F1) rather than Accuracy or ROC-AUC. |
| Reproducibility | Any randomized resampling algorithm MUST accept a random seed AND MUST confine it to a local generator (`np.random.default_rng(seed)`); calling `np.random.seed()` mutates process-wide state and silently couples unrelated stochastic steps in the pipeline. |

## Formula: undoing the undersampling prior shift

Let `beta = kept_majority / original_majority` be the fraction of majority-class
rows retained (minority rows kept in full). Undersampling the negatives by
`beta` multiplies the odds of the positive class by `1 / beta`, so the true
posterior is recovered by scaling the odds back:

```
odds_true = beta * odds_sampled
p         = beta * p_s / (beta * p_s - p_s + 1)
```

The transform is strictly monotone, so it leaves ranking metrics (PR-AUC,
ROC-AUC) untouched and changes only the absolute probability level. This is the
standard prior-correction result for undersampled training sets; see Dal Pozzolo
et al. (2015) and, for the general cost-sensitive treatment, Elkan (2001).

## Sources

| Claim | Source |
|---|---|
| `n_samples / (n_classes * np.bincount(y))` is the `class_weight='balanced'` heuristic | scikit-learn, `sklearn.utils.class_weight.compute_class_weight` API reference — https://scikit-learn.org/stable/modules/generated/sklearn.utils.class_weight.compute_class_weight.html |
| `scale_pos_weight` is a scalar; "A typical value to consider: sum(negative instances) / sum(positive instances)" | XGBoost Parameters documentation — https://xgboost.readthedocs.io/en/stable/parameter.html |
| Precision-Recall curves are more informative than ROC on imbalanced data | Saito T., Rehmsmeier M. (2015), *The Precision-Recall Plot Is More Informative than the ROC Plot When Evaluating Binary Classifiers on Imbalanced Datasets*, PLOS ONE 10(3): e0118432 — https://doi.org/10.1371/journal.pone.0118432 |
| Undersampling warps posterior probabilities and requires correction | Dal Pozzolo A., Caelen O., Johnson R.A., Bontempi G. (2015), *Calibrating Probability with Undersampling for Unbalanced Classification*, IEEE SSCI 2015, pp. 159-166 — https://dblp.org/rec/conf/ssci/PozzoloCJB15.html |
| Threshold/probability adjustment under altered class priors | Elkan C. (2001), *The Foundations of Cost-Sensitive Learning*, IJCAI 2001 |
