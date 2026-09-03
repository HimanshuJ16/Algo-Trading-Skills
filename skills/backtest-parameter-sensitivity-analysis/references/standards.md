# Standards — backtest-parameter-sensitivity-analysis

## The Metric

The analyzer scores the best grid point by the relative Sharpe drop to its worst
immediate neighbour **in parameter order**:

$$\text{degradation} = \frac{S_{\text{best}} - \min(S_{\text{left}}, S_{\text{right}})}{S_{\text{best}}}$$

It is dimensionless, which is the point. A raw gradient $\Delta S/\Delta p$ carries the
parameter's units, so no single cut-off transfers between a lookback measured in days
and a threshold measured in basis points. Any table prescribing a universal "maximum
Sharpe gradient" is therefore unusable as written; the earlier version of this file
contained one, and it was not implemented by the code.

## Configuration Defaults — Not Recommended Limits

| Parameter | Default | Status |
|---|---|---|
| `max_neighborhood_degradation_pct` | 0.15 | Implementation default with **no published basis**. Calibrate against your own backtest's run-to-run Sharpe dispersion: a threshold tighter than the simulator's own noise measures the simulator. |
| `min_viable_sharpe` | 0.0 | Rules out only strategies that lose money at every grid point. Raise it to the deployment hurdle you actually apply. |
| `min_grid_points` | 3 | **Structural**, not tunable downward: a plateau cannot be observed without a neighbour on each side. |

`sharpe_std` is the **population** standard deviation (divisor $N$). The grid is the
whole population of tested configurations, not a sample drawn from one. It is reported
as a description of grid dispersion; it is not used in any verdict.

## Verdict Ladder

Evaluated in order, stopping at the first failure. Ordering matters: viability is
checked before coverage so a losing strategy is never described in plateau language.

| Verdict | Condition | Meaning |
|---|---|---|
| `NOT_VIABLE` | best Sharpe $\le$ `min_viable_sharpe` | A flat grid of unprofitable results is stable, not robust. |
| `INSUFFICIENT_GRID` | fewer than `min_grid_points` | The optimum has no observable neighbourhood. |
| `EDGE_OPTIMUM` | best point is first or last | Only one side observed; the true optimum may lie outside the swept range. |
| `FRAGILE_PEAK` | degradation > threshold | The optimum collapses one grid step away. |
| `ROBUST_PLATEAU` | degradation $\le$ threshold | The optimum survives a one-step perturbation, in-sample, on this one parameter. |

`is_robust` is True only for `ROBUST_PLATEAU`. When several points tie for the maximum,
the most interior one is selected: a perfectly flat grid is the ideal plateau, and
breaking the tie toward the first index would report it as an edge optimum instead.

## Selection Bias — What This Tool Does Not Fix

`best_sharpe` is the **maximum over `total_grid_points` trials**. The maximum of $N$
noisy estimates exceeds their mean by construction, so it overstates expected
out-of-sample performance even for a strategy with no real edge, and the overstatement
grows with $N$. Adding grid resolution makes this worse, not better.

This analyzer does not correct for it. It reports `total_grid_points` precisely so the
correction can be applied downstream. The established treatments:

- **Deflated Sharpe Ratio** — adjusts the significance threshold for the number of
  trials plus the skewness and kurtosis of the return series.
- **Probability of Backtest Overfitting (PBO)** via combinatorially symmetric
  cross-validation — estimates the probability that the selected configuration will
  underperform the median out of sample.
- The repo's own `factor-research-multiple-testing-correction` implements
  Bonferroni / Holm / Benjamini-Hochberg FDR and the Harvey-Liu-Zhu $t \ge 3.0$ hurdle.

Note on sourcing: the two papers below were verified bibliographically (title, authors,
venue, identifier). Their full texts were not retrievable through an open URL during
this review, so no formula from either is reproduced here — apply them from the papers
themselves rather than from a paraphrase.

## Scope Boundary

The analyzer screens **one parameter at a time, in-sample, at one grid resolution**. It
does not sweep joint parameter surfaces, detect interaction effects, deflate the Sharpe,
test out-of-sample stability, or model regime dependence. It takes Sharpe ratios as
given and never computes one, so the return-frequency annualisation convention behind
them is the caller's responsibility and must be consistent across the grid.

## Sources

- Bailey, D. H. and Lopez de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting
  for Selection Bias, Backtest Overfitting and Non-Normality" —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Bailey, D. H., Borwein, J., Lopez de Prado, M. and Zhu, Q. J., "The Probability of
  Backtest Overfitting", *Journal of Computational Finance*, DOI 10.21314/JCF.2016.322 —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
