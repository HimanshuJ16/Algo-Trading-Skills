# Workflows for Handling Rare Signals

1. **Data Splitting**:
   - Perform a strict, time-aware train/validation split *before* any balancing occurs.
   - Example: 2018-2022 (Train), 2023 (Validation).
   - If labels overlap in time (e.g. triple-barrier labels), purge and embargo around the split boundary first. Random undersampling does not remove overlap leakage.
2. **Apply Class Balancing (Training Set ONLY)**:
   - Calculate class distribution on the Training Set.
   - **Method A (Cost-Sensitive)**: Calculate weights and pass them in the shape the estimator documents.
     - `class_weight={0: w0, 1: w1}` from `compute_class_weights(y_train)` for scikit-learn estimators.
     - `scale_pos_weight=<scalar>` from `compute_scale_pos_weight(y_train)` for XGBoost/LightGBM.
   - **Method B (Undersampling)**: Downsample the majority class in the Training Set to a 1:1 or 2:1 ratio (`majority_ratio=1.0` or `2.0`), discarding excess majority rows. Fit the model on this reduced dataset. Record `beta = kept_majority / original_majority`.
3. **Model Inference**:
   - Predict on the unmodified Validation Set.
4. **Probability Correction (Method B, and any time the prior was changed)**:
   - Apply `correct_undersampling_bias(p_s, beta)` to `predict_proba` output before it is read as a real-world probability.
   - Skip this only if the model output is consumed purely as a ranking (top-N selection) or if the decision threshold was tuned directly on the sampled scale.
5. **Scoring**:
   - Evaluate using Precision, Recall, F1-Score, PR-AUC, and the Confusion Matrix.
   - Do NOT use Accuracy.
   - Ranking metrics are identical before and after step 4; report calibration separately (e.g. Brier score or a reliability curve on the untouched validation set).
6. **Comparison Baseline**:
   - Before adopting a rebalanced model, compare it against the same estimator trained on the raw distribution with a tuned decision threshold. Rebalancing is justified only when it beats that baseline on the metric that matters.
