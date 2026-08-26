# Standards for Financial Label Noise Estimation

| Metric | Engineering Standard |
|---|---|
| Prediction Source | Out-of-fold cross-validated probabilities MUST be used for noise detection. |
| Threshold Calculation | Thresholds $t_k$ MUST be calculated independently for each target class as the expected self-confidence of that class (Eqn 2). |
| Collision Handling | When more than one class clears its threshold, the true label MUST be resolved by $\arg\max_{l \in L} \hat{p}(\tilde{y}=l \mid x)$ over the qualifying classes $L$ (Eqn 1). |
| Unconfident Samples | Samples clearing no class threshold MUST be excluded from the confident joint, not counted on the diagonal (Sec. 3.1). |
| Matrix Orientation | The noise transition matrix is $Q_{\tilde{y}\mid y^*}$ (columns sum to 1). The row-normalised form is the *inverse* noise matrix $Q_{y^*\mid\tilde{y}}$; the two MUST NOT be interchanged. |
| Input Domain | Labels MUST be in $\{0, 1\}$ and probabilities MUST be finite and within $[0, 1]$; violations MUST raise rather than propagate. |
| Threshold Comparison | The $\hat{p} \ge t_k$ test MUST tolerate floating-point error. $t_k$ is the mean of the self-confidences compared against it, so a class with near-identical probabilities sits exactly on its threshold and a strict `>=` discards every one of its samples. |
| High Noise Warning | Noise ratio $\eta \ge 20\%$ MUST trigger a high noise warning (inclusive comparison). |

## Primary Source

C. Northcutt, L. Jiang, I. Chuang, "Confident Learning: Estimating Uncertainty in
Dataset Labels", *Journal of Artificial Intelligence Research* **70** (2021)
1373–1411. <https://doi.org/10.1613/jair.1.12125>

Definitions this skill relies on:

| Element | Location | Definition |
|---|---|---|
| Self-confidence | Definition 2 | $\hat{p}(\tilde{y}=i; x \in X_{\tilde{y}=i}, \theta)$ — the predicted probability that a sample belongs to its *given* label. |
| Confident joint $C_{\tilde{y},y^*}$ | Eqn 1 | $\hat{X}_{\tilde{y}=i,y^*=j} := \{x \in X_{\tilde{y}=i} : \hat{p}(\tilde{y}=j;x,\theta) \ge t_j,\ j = \arg\max_{l \in [m]:\ \hat{p}(\tilde{y}=l;x,\theta) \ge t_l} \hat{p}(\tilde{y}=l;x,\theta)\}$ |
| Threshold $t_j$ | Eqn 2 | $t_j = \frac{1}{\lvert X_{\tilde{y}=j}\rvert}\sum_{x \in X_{\tilde{y}=j}} \hat{p}(\tilde{y}=j;x,\theta)$ |
| Calibrated joint $\hat{Q}_{\tilde{y},y^*}$ | Eqn 3 | Row-rescales $C$ to the observed class marginals $\lvert X_{\tilde{y}=i}\rvert$, then normalises the whole matrix to sum to 1. |
| Noise transition matrix | Sec. 3.1 | $\hat{Q}_{\tilde{y}=i\mid y^*=j} := \hat{Q}_{\tilde{y}=i,y^*=j} / \hat{Q}_{y^*=j}$ — the "noisy channel", $p(\tilde{y}\mid y^*)$. |
| Inverse noise matrix | Notation table | $Q_{y^*\mid\tilde{y}}$ — $p(y^*\mid\tilde{y})$, the inverse noise rate. |
| Error selection | Sec. 3.2, CL method 2 | Label errors are the off-diagonal members of $C_{\tilde{y},y^*}$. |

Two behaviours are stated in the paper's prose rather than its equations and are
implemented accordingly:

- Samples with near-uniform predicted probabilities clear no threshold and "will
  not be counted for any class in $C_{\tilde{y},y^*}$" (Sec. 3.1). This is what
  makes CL robust to pure noise and alien-class samples.
- Collisions are resolved by $\arg\max_j \hat{p}(\tilde{y}=j;x,\theta)$ whenever
  $\lvert\{k : \hat{p}(\tilde{y}=k; x \in X_{\tilde{y}=i},\theta) \ge t_k\}\rvert > 1$
  (Sec. 3.1).

## Deviations from the paper

- **Tie-break on exact collisions.** The paper does not specify how to resolve an
  $\arg\max$ tie (e.g. $\hat{p}_0 = \hat{p}_1 = 0.5$). This implementation
  resolves ties in favour of the *observed* label, so an uninformative model
  cannot manufacture label errors. This is a deliberate, documented choice, not a
  result from the source.
- **Binary case only.** The implementation covers $m = 2$; the paper's
  formulation is general over $m$ classes.
- **Rank-and-prune method.** Only CL method 2 (off-diagonals of the confident
  joint) is implemented. The paper also defines Prune by Class, Prune by Noise
  Rate, and their intersection (Sec. 3.2), which rank by predicted probability
  and are not implemented here.
- **Class reweighting.** The paper's training step reweights each class by
  $\hat{Q}_{y^*}[i] / \hat{Q}_{\tilde{y},y^*}[i][i]$. This engine emits per-sample
  prune weights instead; the class weights can be derived from the reported
  `estimated_joint_distribution` if needed.
