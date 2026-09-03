# Standards — walk-forward-hyperparameter-search-budget

## The Budget Formula

$$N_{\text{max}} = \mathrm{clamp}\!\left(\left\lfloor \frac{T_{\text{in}}}{252} \times R \right\rfloor,\; 10,\; 500\right)$$

$R$ is `max_trials_per_year`. The floor is applied *after* multiplication, so the result
truncates: a 250-day window yields 99, not 100. An earlier version of `SKILL.md` gave
this as $\min(100, \lfloor T_{\text{in}}/25 \rfloor \times 10)$, which caps at 100 for
every window length and disagrees with both the implementation and the table below.

## Configuration Defaults — Not Recommended Limits

| Parameter | Default | Status |
|---|---|---|
| `max_trials_per_year` | 100 | House heuristic with **no published basis**. See "Reconciliation with MinBTL" — the literature is far stricter. |
| `MIN_BUDGET` | 10 | Floor. Prevents a short window from producing a budget of zero, which would forbid searching at all rather than forcing a small search. |
| `MAX_BUDGET` | 500 | Ceiling. Caps the budget regardless of data length; not derived from any published result. |
| `HIGH_RISK_OVERRUN_MULTIPLE` | 5 | House convention. Marks where pruning discards the large majority of the designed space. |
| `seed` | 12345 | Arbitrary. Fixed only so pruning is reproducible; any value is equally valid. |

The table below is the repo's own risk guidance, retained for continuity. It is a
convention, not a standard, and no regulator, exchange, or published paper prescribes it.

| Dataset In-Sample Window | Budget under the formula ($R = 100$) | House risk grade |
|---|---|---|
| 1 year (252 days) | 100 | LOW |
| 3 years (756 days) | 300 | LOW |
| 5 years (1,260 days) | 500 (at the cap) | LOW |
| $>5$ years | 500 (cap binds) | MODERATE if the grid exceeds 500 |

## Reconciliation with MinBTL — the House Heuristic Is the Permissive One

Bailey, Borwein, López de Prado and Zhu derive the **Minimum Backtest Length**: the data
span required before selecting the best of $N$ independent zero-skill strategies stops
being expected to produce a given in-sample Sharpe by chance alone.

$$\mathbb{E}[\max_N] = (1-\gamma)\,Z^{-1}\!\left[1 - \tfrac{1}{N}\right] + \gamma\, Z^{-1}\!\left[1 - \tfrac{1}{N e}\right], \qquad \text{MinBTL} = \left(\frac{\mathbb{E}[\max_N]}{SR^{*}}\right)^{2}$$

$\gamma$ is the Euler–Mascheroni constant and $Z^{-1}$ the inverse standard normal CDF.
The paper also states the looser closed form $\text{MinBTL} < 2\ln(N)/(SR^{*})^{2}$; the
Gumbel expression above is the sharper estimate and is what `scripts/search_budgeter.py`
implements.

**Verification status.** The full texts of the source papers were not retrievable as
extractable text through an open URL during this review (the available PDFs are scanned
images). The formula was instead verified *numerically against the source's own published
worked example*: at $SR^{*} = 1$, $N = 45$ returns 5.00 years, matching the paper's
statement that five years of data support no more than forty-five independent model
configurations. That agreement to three significant figures also confirms the denominator
is $\mathbb{E}[\max_N]$ **squared**, which some secondary summaries render without the
exponent.

The two rules disagree sharply, and the disagreement is the point:

| Data available | House budget ($R = 100$) | MinBTL-implied max *independent* trials at $SR^{*} = 1$ |
|---|---|---|
| 1 year | 100 | 3 |
| 3 years | 300 | 13 |
| 5 years | 500 | 45 |
| 10 years | 500 (cap) | 724 |

The house heuristic permits roughly thirty times more trials than MinBTL at one year.
Two things reconcile part of the gap, and neither closes it:

- **Grid points are not independent.** MinBTL counts independent trials. Neighbouring
  points on a smooth parameter surface are highly correlated, so the effective
  independent trial count is well below the raw grid count. This skill does **not**
  estimate that count, and inventing a shrinkage factor for it would be unfounded.
- **MinBTL is a floor for a specific claim** — that the best of $N$ reaches $SR^{*}$ by
  chance. It is not a prohibition on running more configurations; it is a statement about
  what in-sample Sharpe you may no longer treat as evidence.

Where the two disagree, the budget passing is not sufficient. `audit_walk_forward`
reports both so the disagreement is visible rather than resolved silently.

## Scope Boundary

The budgeter counts and bounds **configurations**. It never observes a return series, a
Sharpe ratio, or an out-of-sample result, and therefore cannot compute PBO, deflate a
Sharpe, or detect performance decay. It also cannot see trials conducted outside it —
grids abandoned by eye, prior research on the same data, or a colleague's sweep of the
same instrument. Those count toward selection bias and must be tracked separately.

Duplicate values within an axis are **not** detected: `{"a": [1, 1, 1]}` is three
combinations, and each consumes budget while adding no information. Detection is not
attempted because candidate values need not be hashable — a grid axis of weight vectors
is legitimate — and a comparison-based check would be quadratic in the axis length.
De-duplicate axes before submitting them.

## Sources

- Bailey, D. H., Borwein, J. M., López de Prado, M. and Zhu, Q. J. (2014),
  "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on
  Out-of-Sample Performance", *Notices of the American Mathematical Society*, 61(5) —
  MinBTL and the expected-maximum-Sharpe result —
  <https://www.davidhbailey.com/dhbpapers/backtest-pseudo.pdf>
- Bailey, D. H., Borwein, J. M., López de Prado, M. and Zhu, Q. J., "The Probability of
  Backtest Overfitting", *Journal of Computational Finance*, 20(4), 39–69,
  DOI 10.21314/JCF.2016.322 — PBO via combinatorially symmetric cross-validation —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- Bailey, D. H. and López de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality" —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

## Category

`backtesting-methodology`
