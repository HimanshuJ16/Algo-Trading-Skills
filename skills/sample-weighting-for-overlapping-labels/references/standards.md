# Standards for Sample Weighting for Overlapping Labels

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Label concurrency | $c_t$ MUST count every label whose span covers bar $t$, with **both** endpoints inclusive. | López de Prado (2018), Snippet 4.1 |
| Average uniqueness | $u_i = \frac{1}{\tau_i} \sum_{t=t_{i,0}}^{t_{i,1}} \frac{1}{c_t}$, evaluated over the closed interval $[t_{i,0}, t_{i,1}]$ where $\tau_i$ is its bar count. Result always in $(0,1]$. | López de Prado (2018), Snippet 4.2 |
| Return attribution | $\tilde{w}_i = \left\lvert \sum_{t=t_{i,0}}^{t_{i,1}} \frac{r_t}{c_t} \right\rvert$ with $r_t$ the **log** return of bar $t$ — log returns because the expression sums returns across bars. | López de Prado (2018), Snippet 4.10, p. 69 |
| Time decay | Piecewise-linear in **cumulative uniqueness**, not calendar time: newest observation factor $1$, oldest $\to c$; $c < 0$ zeroes the oldest portion; factors clipped at $0$. | López de Prado (2018), Snippet 4.11, p. 70 |
| Weight normalisation | Weights MUST be rescaled to sum to the sample count $N$ ($w_i \leftarrow w_i N / \sum_j w_j$), and MUST NOT be rounded before that invariant is relied on. | López de Prado (2018), Ch. 4 (`out['w'] *= out.shape[0]/out['w'].sum()`) |
| Bagging sample size | A bagged learner trained on overlapping labels SHOULD have `max_samples` set to the average uniqueness, or use sequential bootstrapping. | López de Prado (2018), §4.4–4.5 |
| Leakage control | Sample weighting MUST NOT be treated as a substitute for purging and embargoing the cross-validation folds. The two address different failures — see "Stated limitations" below. | López de Prado (2018), Ch. 4 vs Ch. 7 |
| Input integrity | Non-finite returns, inverted spans, duplicate sample ids and unrecognised weighting methods MUST raise rather than be coerced. | Repository mandate |

## Verified sources

**López de Prado, M. (2018). _Advances in Financial Machine Learning_. Wiley. Chapter 4, "Sample Weights."**

- *Snippet 4.1, "Estimating the uniqueness of a label"* — `mpNumCoEvents`. Concurrency is accumulated with `count.loc[tIn:tOut] += 1`, a **closed** interval: a label is active on its own closing bar.
- *Snippet 4.2, "Estimating the average uniqueness of a label"* — `mpSampleTW`, `wght.loc[tIn] = (1. / numCoEvents.loc[tIn:tOut]).mean()`. Average uniqueness is the *mean* of the inverse concurrency over the span, not the inverse of the mean concurrency; the two differ whenever concurrency varies within the span.
- *Snippet 4.10, "Determination of sample weight by absolute return attribution"* — `mpSampleW`, `ret = np.log(close).diff()`, then `wght.loc[tIn] = (ret.loc[tIn:tOut] / numCoEvents.loc[tIn:tOut]).sum()` and `return wght.abs()`. The returns are log returns *specifically* so that they are additive across the span.
- *Snippet 4.11, "Implementation of time-decay factors"* — `getTimeDecay(tW, clfLastW=1.)`: `clfW = tW.sort_index().cumsum()`; `slope = (1.-clfLastW)/clfW.iloc[-1]` when `clfLastW >= 0`, else `slope = 1./((clfLastW+1)*clfW.iloc[-1])`; `const = 1.-slope*clfW.iloc[-1]`; `clfW = const + slope*clfW`; `clfW[clfW<0] = 0`. Decay is measured in cumulative uniqueness and the series is sorted by time before the cumulative sum — the factors describe chronology, never argument order.
- *§4.4, "Bagging Classifiers and Uniqueness"* — where average uniqueness is low, a bagged learner's in-bag draws are redundant; the recommended mitigations are setting `max_samples` to the average uniqueness and sequential bootstrapping (§4.5, Snippets 4.3–4.5).

Snippet semantics above were cross-checked against reference reproductions of the book's code: <https://github.com/WongYatChun/Advances-in-Financial-Machine-Learning/blob/master/sampleWeights.py>, and against the Hudson & Thames `mlfinlab` documentation for `get_weights_by_return` / `get_weights_by_time_decay` (<https://www.mlfinlab.com/en/latest/sampling/sample_weights.html>), which states the decay parameter's semantics as: `1` = no decay; `0 < c < 1` = linear decay with all observations keeping positive weight; `0` = weights converge linearly to zero; `c < 0` = the oldest portion receives zero weight.

## Stated limitations

1. **Weighting is not leakage control.** Sample weights correct the non-IID structure *within* a training set. A label window that crosses a fold boundary still puts the validation outcome into training, and that requires purging and embargoing (op. cit. Ch. 7, Snippets 7.1–7.2). The two are complementary, never alternatives: see `hyperparameter-tuning-without-target-leakage` and `walk-forward-validation-setup`.
2. **The $u_i \cdot |r_i|$ fallback is an approximation, not Snippet 4.10.** Without per-bar log returns the engine computes $u_i |r_i|$, which coincides with the published formula only when the span's per-bar returns are uniform. It is reported as such (`return_attribution_is_exact = False`), never presented as the snippet.
3. **No sequential bootstrap.** Snippets 4.3–4.5 (indicator matrix, sequential bootstrap draw) are out of scope. This engine produces weights only.
4. **`TIME_DECAY` composes decay with uniqueness** as $w_i = u_i d_i$. The book applies the decay factors on top of the sample weights; if you want decay over return-attribution weights instead, call `compute_time_decay_factors` and multiply them into the `RETURN_ATTRIBUTED` weights yourself, then re-normalise to $\sum w_i = N$.
5. **Bar indices only.** Concurrency is materialised per index covered, so the map is O(sum of span lengths) in size. Timestamp or tick indices must be converted to bar ordinals first.
6. **No threshold here is a published standard.** The commonly quoted "average uniqueness below 0.5 means you must weight" is a house heuristic, not a rule from the book, which prescribes no cut-off. Read $\bar{u}$ as an effective-sample-size estimate — roughly $\bar{u}N$ independent observations — and weight whenever labels overlap at all, since at $\bar{u} = 1$ the weights are uniform and the correction costs nothing.
