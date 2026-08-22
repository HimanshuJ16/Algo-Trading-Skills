# Pre-Flight Checklist

- [ ] Has the data been split into train/test *before* applying the imbalance handler?
- [ ] Have overlapping labels been purged/embargoed around the split boundary before balancing?
- [ ] Are you using appropriate metrics (F1-score, Precision-Recall AUC) instead of standard Accuracy?
- [ ] If using Undersampling, is a random seed set for reproducibility, and is it confined to a local generator rather than `np.random.seed()`?
- [ ] Are class weights calculated correctly, giving heavily increased weights to the minority class?
- [ ] Is the weight passed in the shape the estimator expects — a `{label: weight}` dict for scikit-learn `class_weight`, a scalar `negatives/positives` for XGBoost `scale_pos_weight`?
- [ ] Was `beta` (retained majority fraction) recorded at undersampling time?
- [ ] Are predicted probabilities corrected for the prior shift before they drive sizing, expected value, or an absolute threshold?
- [ ] Does the rebalanced model actually beat the raw-distribution model with a tuned threshold?
