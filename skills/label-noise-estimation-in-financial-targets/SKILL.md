---
name: label-noise-estimation-in-financial-targets
description: >-
  Use when a binary financial target such as a triple-barrier or fixed-horizon label is
  mislabelled by microstructure noise; confident learning estimates the noise transition
  matrix and flags suspect samples before training.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: label-noise, confident-learning, cleanlab, financial-ml, target-labeling, noise-ratio, sample-weighting
  brokers_frameworks: "Cleanlab Framework; scikit-learn / XGBoost; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when training financial machine learning models (XGBoost, LightGBM, Neural Networks) on binary classification targets (Triple-Barrier Method, Trend Scanning, Fixed-Horizon Returns). Financial targets suffer from severe **label noise** due to microstructure noise, bid-ask spread bounces, and volatile market jumps. Training ML models on uncleaned, noisy labels leads to catastrophic overfitting and negative out-of-sample Sharpe ratios. This module estimates the label noise ratio $\eta$, identifies mislabeled training samples via Confident Learning, and outputs cleaned target vectors and sample weights.

## When NOT to Use

- **Multi-class or regression targets.** This engine implements the binary ($m = 2$) case only and rejects labels outside $\{0, 1\}$. Meta-labelling with a neutral class, or continuous return targets, need the general $m \times m$ formulation.
- **Without out-of-fold probabilities.** If you only have in-sample predictions, the thresholds become self-fulfilling and the noise ratio is systematically understated. Generate out-of-fold probabilities first — with purged/embargoed CV for overlapping labels.
- **When the model is uninformative.** If the classifier is near-random, the confident joint is nearly empty and the noise estimate carries no signal. Check that the out-of-fold model has genuine predictive power before acting on $\eta$.
- **As a substitute for fixing the labelling scheme.** A persistently high $\eta$ usually means the barriers, horizon, or volatility scaling are mis-specified — not that individual samples need deleting.

## Prerequisites

- Array of noisy observed target labels $y \in \{0, 1\}$.
- Vector of **out-of-fold** cross-validated predicted probabilities $P(y=1|x)$, finite and within $[0, 1]$.

## Workflow

1. **Class-Specific Threshold Calculation**:
   - Compute the expected self-confidence threshold $t_k$ for each class:
     $$t_k = \frac{1}{|X_{\tilde{y}=k}|} \sum_{x \in X_{\tilde{y}=k}} \hat{p}(\tilde{y}=k \mid x)$$
2. **Confident Error Identification** (confident joint, Eqn 1):
   - Collect the classes that clear their own threshold: $L = \{ l : \hat{p}(\tilde{y}=l \mid x) \ge t_l \}$.
   - If $L = \emptyset$, the sample is **excluded** from the confident joint entirely — it is neither counted nor pruned. Do not treat it as clean evidence.
   - If $|L| > 1$ (a *collision*), resolve it by $\arg\max_{l \in L} \hat{p}(\tilde{y}=l \mid x)$. Skipping this step is the single most common way to break Confident Learning: a low $t_k$ on a corrupted class otherwise flags samples the model strongly agrees with.
   - The sample is a confident error only if the resolved label differs from the observed label.
3. **Noise Ratio & Matrix Estimation**:
   - Compute total noise ratio $\eta = \frac{N_{\text{mislabeled}}}{N_{\text{total}}}$.
   - If $\eta \ge 0.20$ ($20\%$) $\implies$ Flag `HIGH_LABEL_NOISE_WARNING` (inclusive comparison).
   - Calibrate the joint (Eqn 3), then derive the **noise transition matrix** $P(\tilde{y} \mid y^*)$ by column-normalising and the **inverse noise matrix** $P(y^* \mid \tilde{y})$ by row-normalising. These are different matrices — check which one a downstream loss-correction method expects.
4. **Noise-Cleaned Target & Sample Weight Generation**:
   - Choose **one** remediation strategy: either train on `y_clean` (confident errors relabelled) with uniform weights, or train on the original labels with $W_i = 0.0$ for confident errors. Applying both cancels out.
5. **Audit Report Generation**: Output structured `LabelNoiseReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Training on In-Sample Probabilities**: Computing Confident Learning thresholds on in-sample predicted probabilities instead of out-of-fold cross-validated probabilities, understating noise ratios.
- **Dropping the Collision `arg max`**: Flagging a sample as mislabeled purely because the opposite class clears its threshold. When one class is heavily corrupted its threshold collapses, so almost every sample of the other class clears it — including samples the model assigns $0.88$ to their own observed label. Confident Learning requires the $\arg\max$ tie-break over all qualifying classes.
- **Counting Unconfident Samples as Clean**: Folding samples that clear *no* class threshold into the confident joint inflates the diagonal and biases the transition matrix. They must be excluded and reported separately.
- **Confusing the Two Noise Matrices**: $P(\tilde{y} \mid y^*)$ (noise transition, columns sum to 1) and $P(y^* \mid \tilde{y})$ (inverse noise, rows sum to 1) are not interchangeable. Forward loss correction needs the former; feeding it the latter silently corrupts training.
- **Applying Relabelling and Pruning Together**: Zeroing the weights of the samples you just relabelled discards the relabelling entirely and shrinks the dataset for no benefit.
- **Ignoring Class Imbalance in Noise Thresholds**: Using a fixed $0.5$ threshold across imbalanced financial classes instead of class-specific expected probabilities $t_k$.
- **Hard Pruning All Low-Confidence Data**: Dropping too many samples when the noise ratio is low, reducing training dataset size unnecessarily.

## Verification

- Instantiate `LabelNoiseEstimatorEngine` and feed a target vector with known injected errors. Verify the reported `mislabeled_indices` are exactly the injected ones, that samples the model confidently agrees with are never flagged, and that samples clearing no threshold appear in `unconfident_samples_count` rather than in the error list.
- Confirm matrix orientation: columns of `noise_transition_matrix` sum to $1$, rows of `inverse_noise_matrix` sum to $1$, and `estimated_joint_distribution` sums to $1$.
- Confirm the boundary: $\eta$ exactly at $20.0\%$ $\implies$ `HIGH_LABEL_NOISE_WARNING`; $\eta$ below $\implies$ `TARGET_NOISE_CLEANED`.
- Confirm invalid input is rejected: non-binary labels, NaN/Inf probabilities, and probabilities outside $[0, 1]$ must raise `ValueError` rather than propagate silently.
- Run `python -m unittest discover -s skills/label-noise-estimation-in-financial-targets/scripts`.

## Related Skills

- `synthetic-labels-from-triple-barrier-method`
- `sample-weighting-for-overlapping-labels`
- `hyperparameter-tuning-without-target-leakage`
- `class-imbalance-handling-for-rare-signal-events`
- `factor-research-multiple-testing-correction`
