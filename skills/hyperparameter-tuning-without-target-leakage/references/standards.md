# Standards for Leakage-Free Hyperparameter Tuning

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Nesting | Hyperparameter selection MUST occur in an inner loop whose data is disjoint from the outer block used to report performance. | Varma & Simon (2006); Cawley & Talbot (2010) |
| Purging | Training observations whose label interval overlaps the validation interval MUST be removed, at **both** the inner and the outer level. | López de Prado (2018), Snippet 7.1, p. 106 |
| Embargo | A buffer of $\lceil n \cdot E \rceil$ observations MUST follow each validation block, $E = 1\%$ by default. | López de Prado (2018), Snippet 7.2 |
| Preprocessing isolation | Every stateful transform — scaler, encoder, imputer, feature selector — MUST be fitted on the training indices of the current fold only. **Enforced by the caller, not by this engine.** | Kaufman et al. (2012), §3.2 |
| Determinism | An identical `(n_samples, param_grid, evaluation callback)` triple MUST produce an identical report. No unseeded randomness. | Repository mandate |
| Reported statistics | Every figure in the report MUST be derived from the caller's evaluation callback or from a closed-form expression. No simulated, assumed or randomly drawn values. | Repository mandate |

## Verified sources

**López de Prado, M. (2018). _Advances in Financial Machine Learning_. Wiley. Chapter 7, "Cross-Validation in Finance."**

- *Snippet 7.1, p. 106* — purging. Overlap is tested with **inclusive** bounds across three cases: the training label starts within the test interval, ends within it, or envelops it. A training label ending exactly on the first test bar is therefore purged. With a fixed $h$-bar forward label over $[i, i+h]$, that is exactly the $h$ observations $[\text{val\_start} - h, \text{val\_start})$.
- *Snippet 7.2* — embargo, sized `step = int(times.shape[0] * pct_embargo)`, with the reference wrapper defaulting to `pct_embargo=0.01`.
- *Snippet 7.4, p. 110* — `PurgedKFold.split`, embargo applied to the right-hand training segment only.

> **Documented deviation.** Snippet 7.2's `int(...)` truncates to zero whenever $T \cdot E < 1$ — that is, for any sample under 100 bars at the customary 1% — silently disabling the embargo. `LeakageFreeHyperparameterTunerEngine.embargo_window` uses $\lceil \cdot \rceil$ instead, so a positive `embargo_pct` always buffers at least one bar. This is strictly more conservative than the published snippet and is **not** a reproduction of it.

**Varma, S. & Simon, R. (2006). "Bias in error estimation when using cross-validation for model selection." _BMC Bioinformatics_ 7:91. DOI 10.1186/1471-2105-7-91.**

On simulated "null" datasets with no difference between classes — true error rate 50% — the CV error of the tuned classifier averaged 37.8% for shrunken centroids and 41.7% for SVM; it fell below 30% on 18.5% and 38% of null datasets respectively. The authors report that "the nested CV procedure reduces the bias considerably and gives an estimate of the error that is very close to that obtained on the independent testing set."

**Cawley, G. C. & Talbot, N. L. C. (2010). "On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation." _Journal of Machine Learning Research_ 11(70), 2079–2107.** <https://www.jmlr.org/papers/v11/cawley10a.html>

From the abstract: "we show that some common performance evaluation practices are susceptible to a form of selection bias as a result of this form of over-fitting and hence are unreliable," and that "the degradation in performance due to over-fitting the model selection criterion can be surprisingly large" — "of comparable magnitude to differences in performance between learning algorithms." The remedy discussed is nested (double) cross-validation with separate inner and outer resampling loops.

**Bailey, D. H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." _Journal of Portfolio Management_ 40(5), 94–107.** <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

The expected maximum Sharpe ratio over $N \gg 1$ independent trials:

$$E\left[\max_n \widehat{SR}_n\right] \approx E\left[\widehat{SR}\right] + \sqrt{V\left[\widehat{SR}\right]}\left[(1-\gamma)\,Z^{-1}\!\left(1 - \tfrac{1}{N}\right) + \gamma\,Z^{-1}\!\left(1 - \tfrac{1}{N e}\right)\right]$$

with $\gamma \approx 0.5772$ the Euler–Mascheroni constant and $Z^{-1}$ the standard normal quantile function. Implemented as `expected_max_sharpe_under_null`, using the cross-candidate dispersion of the inner-CV scores as $\sqrt{V[\widehat{SR}]}$ and $N$ = grid size.

**Kaufman, S., Rosset, S., Perlich, C. & Stitelman, O. (2012). "Leakage in Data Mining: Formulation, Detection, and Avoidance." _ACM TKDD_ 6(4), Article 15.**

Cited for the no-time-machine requirement governing preprocessing isolation. See `feature-engineering-without-leakage` for the treatment of feature-level leakage, which this skill does not address.

## Stated limitations

1. **The engine controls index sets, not computation.** `structural_isolation_verified` is a set-intersection check over the indices the engine authorised. An evaluation callback that ignores them, or that reads a feature column built with future information, leaks anyway and the flag will still read `True`.
2. **The leaky baseline is a floor, not an estimate.** `leaky_cv_overestimated_sharpe` runs contiguous K-Fold with no purge, embargo or nesting. Shuffled K-Fold — the more common mistake — destroys time ordering as well and leaks strictly more.
3. **A zero or negative haircut proves nothing.** It most often means the callback's score is insensitive to its training set. The engine logs a warning rather than presenting it as a clean result.
4. **The luck floor assumes independent trials.** Grid points are typically correlated, so the effective $N$ is below the grid size and the reported floor is conservative in that direction.
5. **Fixed-horizon purging only.** `purge_window_samples` is a constant bar count. Variable-horizon labels (triple-barrier outcomes that resolve at different times per observation) need per-observation label end times; see `synthetic-labels-from-triple-barrier-method`.
