# Standards — feature-selection-stability-across-folds

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry or academic standards. The source
paper defines the estimator, proves its properties and derives its sampling
distribution, but it prescribes **no threshold** at which a feature set is declared
stable. No regulator or standards body publishes one either. The right values depend
on how many folds you can afford, how many candidate features you screen, and what a
false promotion costs. Calibrate each against your own validation record and write
down the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| Stability threshold ($\Phi_{\min}$) | $0.70$ | Cut-off for the `STABLE_FEATURE_SET` verdict, and the null value $\Phi_0$ of the one-sided test. A house convention, not a published number. |
| Consensus inclusion threshold ($p_{\min}$) | $0.80$ | A feature is retained when selected in at least $\lceil p_{\min}K \rceil$ folds. Applied on integer fold counts, so at $K=5$ it means 4 folds and at $K=3$ it means all 3. |
| Minimum folds ($K$) | $5$ | Below this the engine warns. The estimator itself only requires $K \ge 2$; the confidence interval is asymptotic in $K$ and is unreliable on very few folds. |
| Confidence level | $95\%$ | Two-sided coverage of the reported interval; the one-sided test runs at $\alpha = 5\%$. |

## Estimator facts (verified against the primary source)

Source: Sarah Nogueira, Konstantinos Sechidis and Gavin Brown, **"On the Stability of
Feature Selection Algorithms"**, *Journal of Machine Learning Research*, vol. 18
(2018), pp. 1–54 — <https://jmlr.org/papers/v18/17-514.html>.
Authors' reference implementation: <https://github.com/nogueirs/JMLR2018>
(`python/stability`). The paper writes $d$ for the number of features and $M$ for the
number of feature sets; this skill writes $M$ and $K$ respectively.

| Fact | Location in the paper |
|---|---|
| $\hat{\Phi}(Z) = 1 - \dfrac{\frac{1}{d}\sum_{f} s_f^2}{\frac{\bar{k}}{d}\left(1 - \frac{\bar{k}}{d}\right)}$, with $s_f^2 = \frac{M}{M-1}\hat{p}_f(1-\hat{p}_f)$ | Definition 4 |
| $\hat{\Phi} = 1$ **if and only if** all $M$ feature sets are identical | Sec. 4.1 (verification of the Bounds property) |
| Lower bound of $\hat{\Phi}$ is $-\frac{1}{M-1}$; asymptotically bounded by 0 as $M \to \infty$ | Sec. 4.1, Appendix D |
| $\mathbb{E}[\hat{\Phi} \mid H_0] = 0$ under the Null Model of Feature Selection — the estimator is corrected for chance | Definition 2, Theorem 3, Sec. 4.1 |
| $\hat{\Phi}$ is **undefined** when $\bar{k} = 0$ or $\bar{k} = d$, because the denominator is zero | Sec. 4.1, immediately after Definition 4 |
| Average pairwise intersection over the $M(M-1)$ ordered pairs equals $\bar{k} - \sum_f s_f^2$ — an independent route to the same value | Theorem 1 |
| $\hat{\Phi}$ reduces to Kuncheva's (2007) consistency index when every feature set has the same cardinality, and equals Fleiss' Kappa in the binary case | Theorems 5 and 6 |
| Asymptotic normality and the variance estimator $v(\hat{\Phi}) = \frac{4}{M^2}\sum_i (\hat{\Phi}^{(i)} - \bar{\hat{\Phi}})^2$ | Theorem 7 |
| Approximate confidence interval $\hat{\Phi} \pm z_{(1-\alpha/2)}\sqrt{v(\hat{\Phi})}$ | Corollary 8 |
| One-sided test of $H_0: \Phi = \Phi_0$ against $H_1: \Phi > \Phi_0$ using $V_M = (\hat{\Phi} - \Phi_0)/\sqrt{v(\hat{\Phi})}$ | Sec. 4.2.4 |
| The sampling distribution assumes each row of $Z$ is an **independent** sample; the paper measures stability over bootstrap resamples | Sec. 4.2.2, Sec. 2 |

`scripts/feature_stability_analyzer.py` implements Definition 4, Theorem 7,
Corollary 8 and the Section 4.2.4 test. Its outputs were cross-validated against the
authors' reference implementation over 400 randomised selection matrices, agreeing to
within $10^{-15}$ on the stability, its variance, both interval bounds and the test
p-value. The test suite additionally re-derives $\Phi$ through Theorem 1 and through
Kuncheva's index, which share no arithmetic with the implementation.

## Known limitations

- **Independence is assumed and financial CV violates it.** Walk-forward and purged
  k-fold splits of one price series share training data and are serially dependent.
  $\Phi$ is therefore optimistic and the confidence interval is narrower than the
  truth. Treat both as an upper bound on the evidence, not as calibrated statistics.
- **Asymptotics are in the number of folds, not rows per fold.** The interval is
  approximate at the fold counts typical of walk-forward validation. Adding rows to
  each fold does not tighten it; adding folds does.
- **Stability is not predictive utility.** $\Phi = 1$ is attainable by a constant
  selector, including a broken one. It is a necessary condition for trusting a
  feature set, never a sufficient one.
- **$\Phi$ depends on the candidate pool.** It is normalised by the number of
  features the selector could have chosen. Two runs are comparable only if they
  screened the same pool.
- **Selection uses every fold.** Any performance figure computed on the same folds
  that produced the consensus set is selection-biased; re-validate out of sample.
