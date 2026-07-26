# Pre-Flight Checklist

- [ ] Has the data been split into train/test *before* applying the imbalance handler?
- [ ] Are you using appropriate metrics (F1-score, Precision-Recall AUC) instead of standard Accuracy?
- [ ] If using Undersampling, is a random seed set for reproducibility?
- [ ] Are class weights calculated correctly, giving heavily increased weights to the minority class?
