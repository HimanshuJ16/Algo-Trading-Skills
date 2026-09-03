# Transfer Learning Across Correlated Instruments — Methods and Sources

## 0. What is a standard here, and what is not

**No regulator, exchange or standards body publishes thresholds for source-asset
selection, domain-shift tolerance, or transfer approval.** Every numeric threshold in
this skill is an operational default chosen to be defensible, not an authority to cite.
The formulas in §2 are published methods with sources; the numbers in §1 and §3 are
desk policy and must be calibrated on your own instruments, features and horizon.

An earlier version of this file presented a "Source-Target Asset Correlation & Selection
Matrix" of minimum correlations by asset class as though it were an institutional
standard. It was not sourced, and it has been removed rather than re-cited.

## 1. Source-asset selection — considerations, not thresholds

Choosing a source instrument is a judgement about shared economic drivers. The
questions worth answering, per target class:

| Target | Plausible source | What actually has to hold |
| :--- | :--- | :--- |
| New single stock (IPO) | Sector ETF, or a close listed comparable | Shared sector and factor loadings; comparable market cap and float; the comparable is not itself dominated by the target's own listing |
| New token | An established large-cap token, or a basket | Shared ecosystem or liquidity venue; comparable market depth; correlations here are unstable across regimes and must be re-measured, not assumed |
| Thinly traded corporate bond | Liquid IG/HY ETF, or an issuer curve point | Comparable duration and credit quality; note that ETF prices and bond marks are struck differently, so the alignment problem is real before the correlation is |
| Exotic FX pair | A G10 pair sharing one leg, or a regional basket | Shared base or quote currency, or a shared trade basket; watch for pegs and managed floats, where the correlation is a policy artefact that can end on an announcement |

Whatever the pairing, the empirical gates in §3 are what decide, and the out-of-sample
comparison overrides both.

## 2. The methods, with sources

### 2.1 Source pre-training — ordinary least squares

Over source-standardized features `Z`:

```
L_src(w, b) = (1/N_src) * sum_i (y_i - z_i . w - b)^2
```

Solved in closed form from the centred normal equations. The scaler
(`feature_means`, `feature_stds`, population standard deviation) is retained and reused
for every downstream target model.

### 2.2 Target fine-tuning — L2-SP

```
L_tgt(w, b) = (1/N_tgt) * sum_j (y_j - z_j . w - b)^2 + lambda * ||w - w_src||^2
```

The penalty is toward the *pre-trained weights*, not toward the origin. This is L2-SP:

> Li, X., Grandvalet, Y. & Davoine, F. (2018). "Explicit Inductive Bias for Transfer
> Learning with Convolutional Networks." *Proceedings of the 35th International
> Conference on Machine Learning*, PMLR 80:2825–2834.
> https://proceedings.mlr.press/v80/li18a.html

Profiling out the intercept (`b = ybar - zbar . w`) and centring gives the exact solution

```
( Zc^T Zc / N + lambda * I ) w = Zc^T yc / N + lambda * w_src
```

Two consequences worth stating explicitly:

- For `lambda > 0` the system is positive definite for any `N`, so the transferred model
  is identified where an unregularized target-only fit is not. This is the property that
  makes the method useful on a cold start.
- `lambda` is defined against the **mean** squared error, so its influence does not decay
  as the target's history grows: shrinkage is governed by `lambda` alone, invariant to
  `N`. A desk wanting the prior to wash out asymptotically must scale `lambda` with
  `1 / N_tgt` itself.

An iterative implementation that averages the data-fit gradient over `N` while applying
the penalty gradient once per sample imposes an effective penalty of `N * lambda`, which
makes the configured `lambda` mean something different for every instrument. The closed
form removes the possibility.

### 2.3 Domain distance — standardized mean difference

```
SMD_d = |mu_{d,src} - mu_{d,tgt}| / sigma_{d,src}       (per feature, sample sd)
Delta_domain = (1/D) * sum_d SMD_d                       (the scalar gate)
```

This is a **standardized mean difference**, the covariate-balance diagnostic of the
propensity-score literature. It is **not** a Wasserstein distance. The first Wasserstein
distance is

```
W_1(u, v) = inf_{pi in Gamma(u,v)} integral |x - y| dpi(x, y)
          = integral |U(x) - V(x)| dx
```

over the two CDFs (SciPy, `scipy.stats.wasserstein_distance`), and is sensitive to
differences in dispersion and shape that an SMD cannot see: two distributions with equal
means and wildly different variances score 0.0 under an SMD. If dispersion matters for
your features, use a distributional distance in addition.

For scale, the covariate-balance literature treats an SMD below 0.1 as well balanced and
below 0.25 as acceptable — conventions attributed to Austin (2009, 2011), not statistical
laws, and set for causal inference rather than transfer learning. The default ceiling of
2.0 in this skill is far more permissive, because some shift is expected between two
different instruments; it is a starting point to tighten, not a bar that has been cleared.

An SMD compares marginals of `X` only. **Covariate shift** in the sense of

> Shimodaira, H. (2000). "Improving predictive inference under covariate shift by
> weighting the log-likelihood function." *Journal of Statistical Planning and
> Inference* 90(2), 227–244.

is the case `p_src(x) != p_tgt(x)` **with** `p_src(y|x) = p_tgt(y|x)`. Nothing in this
skill tests the second half. Target-return correlation is a different and weaker claim.

### 2.4 Out-of-sample scoring — Campbell–Thompson

```
R2_oos = 1 - sum (y_t - yhat_t)^2 / sum (y_t - ybar_fit)^2
```

where `ybar_fit` is the historical mean over the **fit** window, not the evaluation
window's own mean.

> Campbell, J. Y. & Thompson, S. B. (2008). "Predicting Excess Stock Returns Out of
> Sample: Can Anything Beat the Historical Average?" *Review of Financial Studies*
> 21(4), 1509–1531.

Negative values are meaningful and common: they say the model lost to the historical
mean. Campbell and Thompson also note that out-of-sample R-squared of well under 1% can
still be economically meaningful for a mean-variance investor — do not read a small
positive number as a failure, or a large one as a success without asking where it came
from.

### 2.5 Correlation precision — Fisher z

`z = artanh(r)` is approximately normal with standard error `1 / sqrt(n - 3)`, giving a
95% lower bound of `tanh(z - 1.96 / sqrt(n - 3))`. Reported alongside the point estimate
because a correlation floor cleared on a dozen bars establishes very little.

### 2.6 Negative transfer

> Zhang, W., Deng, L., Zhang, L. & Wu, D. (2022). "A Survey on Negative Transfer."
> *IEEE/CAA Journal of Automatica Sinica*. https://arxiv.org/abs/2009.00909

Defines negative transfer as the case where "leveraging source domain data/knowledge
undesirably reduces the learning performance in the target domain." The question of when
transfer helps at all was posed in Rosenstein, M. T., Marx, Z., Kaelbling, L. P. &
Dietterich, T. G. (2005), "To Transfer or Not To Transfer," *NIPS 2005 Workshop on
Inductive Transfer*.

## 3. Gate defaults — calibrate these

| Gate | Default | Basis |
| :--- | :--- | :--- |
| `min_correlation` | 0.60 | Operational default. No source. |
| `min_correlation_overlap` | 30 | Operational default, chosen so the Fisher-z interval is not absurdly wide. No source. |
| `l2_penalty` (lambda) | 0.1 | Operational default. Calibrate on the target's held-out window. |
| `max_domain_shift` (mean SMD) | 2.0 | Operational default, deliberately permissive; §2.3 for the contrasting causal-inference convention of 0.1–0.25. |
| `max_feature_domain_shift` | `None` (off) | Recommended on. The mean across `D` features hides one badly shifted feature. |
| `test_fraction` | 0.30 | Operational default. |
| `min_test_samples` | 5 | Floor below which a held-out R-squared is not worth reporting. Operational default. |

## 4. Deployment rules enforced in code

A transfer is recommended only when **all** hold; each failure is returned in
`rejection_reasons` with its measured value:

1. Aligned overlap `>= min_correlation_overlap`.
2. Aligned correlation `>= min_correlation`.
3. Mean SMD `<= max_domain_shift`, and worst-feature SMD `<= max_feature_domain_shift`
   when configured.
4. `transfer_model_r2 > 0` — the model beats the fit-window historical mean out of
   sample.
5. `transfer_model_r2 > direct_target_r2` where the target-only baseline is identified.

Rule 4 is not redundant with rule 5. Out of sample both scores can be negative, and
"beats the baseline" is then satisfied by a model that is itself worse than predicting
the mean.
