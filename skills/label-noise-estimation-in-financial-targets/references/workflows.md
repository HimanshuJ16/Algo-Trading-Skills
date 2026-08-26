# Workflows for Label Noise Estimation

1. **Out-of-Fold Prediction Ingestion**:
   - Ingest observed noisy targets $\tilde{y} \in \{0,1\}$ and cross-validated
     out-of-fold predicted probabilities $\hat{p}(\tilde{y}=1 \mid x)$.
   - Use purged and embargoed cross-validation when the labels overlap in time,
     otherwise the "out-of-fold" probabilities still leak.
   - Reject non-binary labels and non-finite or out-of-range probabilities before
     any statistic is computed.
2. **Threshold Estimation (Eqn 2)**:
   - $t_k$ is the mean self-confidence of the samples *observed* as class $k$.
   - If a class has no observed samples, $t_k$ is undefined; the engine falls back
     to $0.5$ and logs a warning. Noise estimates on a single-class target vector
     are not meaningful.
3. **Confident Learning Error Detection (Eqn 1)**:
   - Build the qualifying set $L = \{l : \hat{p}(\tilde{y}=l \mid x) \ge t_l\}$.
   - $L = \emptyset$ → exclude the sample from the confident joint; count it as
     unconfident.
   - $|L| = 1$ → the single qualifying class is the estimated true label.
   - $|L| > 1$ → collision; take $\arg\max_{l \in L} \hat{p}(\tilde{y}=l \mid x)$,
     breaking exact ties in favour of the observed label.
   - Record the sample in $C[\tilde{y}][y^*]$. Off-diagonal placements are the
     confident errors.
4. **Joint Calibration & Matrix Derivation (Eqn 3)**:
   - Rescale each row of $C$ back to the observed class count, then normalise to a
     distribution $\hat{Q}$. This corrects for classes that lost unequal numbers
     of samples to the unconfident exclusion.
   - Column-normalise $\hat{Q}$ for the noise transition matrix
     $P(\tilde{y} \mid y^*)$; row-normalise for the inverse noise matrix
     $P(y^* \mid \tilde{y})$.
5. **Noise Ratio & Remediation**:
   - $\eta = N_{\text{mislabeled}} / N_{\text{total}}$, where the denominator is
     the full sample count including unconfident samples.
   - $\eta \ge$ the configured threshold (default $20\%$) raises
     `HIGH_LABEL_NOISE_WARNING`.
   - Pick exactly one remediation path — relabel using `y_clean`, or prune using
     `sample_weights`. Combining them nullifies the relabelling.
6. **Audit Report Generation**:
   - Output the structured `LabelNoiseReport`, retaining the raw confident joint
     counts and the calibrated joint so the two conditional matrices can be
     re-derived and audited independently.
