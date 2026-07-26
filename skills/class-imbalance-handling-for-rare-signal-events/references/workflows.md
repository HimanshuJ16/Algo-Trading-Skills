# Workflows for Handling Rare Signals

1. **Data Splitting**:
   - Perform a strict, time-aware train/validation split *before* any balancing occurs.
   - Example: 2018-2022 (Train), 2023 (Validation).
2. **Apply Class Balancing (Training Set ONLY)**:
   - Calculate class distribution on the Training Set.
   - **Method A (Cost-Sensitive)**: Calculate class weights and pass them to the ML estimator during the `.fit()` call.
   - **Method B (Undersampling)**: Downsample the majority class in the Training Set to achieve a 1:1 or 2:1 ratio, discarding excess majority rows. Fit the model on this reduced dataset.
3. **Model Inference**:
   - Predict on the unmodified Validation Set.
4. **Scoring**:
   - Evaluate using Precision, Recall, F1-Score, and the Confusion Matrix.
   - Do NOT use Accuracy.
