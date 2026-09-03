# Institutional Standards — benchmark-relative-performance-attribution

## Conventions this skill commits to

Attribution numbers are not comparable across reports unless the conventions match.
State these alongside any figure.

| Convention | This skill | Common alternative |
|---|---|---|
| Alpha annualization | Arithmetic: $\alpha_{\text{period}} \times N$ | Geometric: $(1 + \alpha_{\text{period}})^N - 1$, used by `empyrical`/`pyfolio` |
| Risk-free conversion | Simple division: $R_f / N$ | Compounded: $(1 + R_f)^{1/N} - 1$ |
| Tracking error | Sample stdev of active returns (`ddof=1`) $\times \sqrt{N}$ | Population stdev (`ddof=0`) |
| Information ratio | Annualized active return / annualized $TE$ = $IR_{\text{period}} \sqrt{N}$ | Regression-residual (appraisal-ratio) form |
| Brinson variant | Brinson-Fachler | Brinson-Hood-Beebower |
| Interaction effect | Reported separately | Folded into selection |
| Brinson weights | Start of period | End of period (incorrect) |

The arithmetic/geometric alpha gap is small at typical magnitudes and grows with alpha:
5.00% arithmetic is 5.13% geometric at $N = 252$. Neither is wrong; an undisclosed
choice is.

## Interpretation thresholds

| Metric | Reference level | What it actually means |
|---|---|---|
| Annualized alpha $\alpha$ | $> 0$ | Return not explained by benchmark exposure. A **point estimate** — the sign alone is not evidence. |
| Beta $\beta$ | Mandate-dependent | Sensitivity to the benchmark. A "target range" like $0.8$–$1.2$ is a mandate constraint, not a property of good strategies; market-neutral mandates target $\approx 0$. |
| Information ratio $IR$ | $0.5$ good, $0.75$ very good, $1.0$ exceptional | Grinold & Kahn's cross-sectional table of *manager percentiles* ($IR = 0.5 \approx$ 75th percentile, $1.0 \approx$ 90th). It ranks managers; it does not establish that a given IR is statistically distinguishable from zero. |
| $t$-statistic of active return | $\lvert t \rvert \ge 1.96$ | 95% two-sided significance. $t = IR \cdot \sqrt{\text{years}}$, so an $IR$ of $0.5$ needs roughly 15 years to clear 1.96. Report it next to the IR. |
| Brinson selection effect | $> 0$ | Security selection contributed positively **in that single period**. Not evidence of persistent skill. |

The $t = IR \sqrt{T}$ identity follows directly from the definitions: with $n$ observations
over $T = n/N$ years, $t = \sqrt{n}\,\bar{D}/s_D$ and $IR = \sqrt{N}\,\bar{D}/s_D$. It assumes
serially independent active returns — autocorrelation inflates both.

## Degeneracy handling — undefined is never zero

| Condition | Correct treatment | This engine |
|---|---|---|
| Benchmark series constant | Beta is **not identified** by the data, so CAPM alpha is undefined too. Reporting $\beta = 0$ or $1$ is a *convention* and must be labelled as one, never presented as a measurement. | Raises `AttributionError`. |
| Either series constant | Correlation is 0/0 — **undefined**, not zero. | Reports `nan` with a warning. |
| Active return constant (zero tracking error) | The information ratio is **unbounded**, not zero. Reporting `0.0` makes a perfectly consistent outperformer indistinguishable from a manager with no skill. | Reports $\pm\infty$ with a warning; `0.0` only when active return is also zero. |
| Degeneracy tolerances | Must sit at the floating-point noise floor (per-period $\sigma \lesssim 10^{-12}$). A tolerance at a plausible financial magnitude — e.g. `variance > 1e-8` — misclassifies a genuine low-volatility benchmark (daily $\sigma \approx 1$bp) as constant and reports $\beta = 0$ alongside a correlation of 1.0. | `1e-12` throughout. |
| NaN/Inf in either series | Reject. NaN propagates silently through covariance and mean, producing output that looks numeric but is meaningless — and flips `is_alpha_positive` to `False`, which reads as a legitimate fail. | Raises `AttributionError`. |

Every caveat that applies to a result is surfaced in `AttributionSummary.warnings`, not
only logged: sub-one-year sample, thin sample, $\lvert t \rvert < 1.96$, undefined
correlation, unbounded information ratio.

## Benchmark appropriateness (GIPS)

The CFA Institute *Guidance Statement on Benchmarks for Firms* (effective 1 April 2021)
sets out what makes a benchmark appropriate: **specified in advance, relevant, measurable,
unambiguous, representative of current investment options, accountable, investable, and
complete**. Practical requirements relevant here:

- **Total returns only.** All composite, pooled fund and benchmark returns in GIPS Reports
  must be total returns (including income). Comparing a total-return portfolio to a
  price-only index manufactures alpha roughly equal to the index dividend yield.
- **An appropriate benchmark must be selected where one is available**; where the firm
  determines no appropriate benchmark exists, it must disclose why none is presented.
- **Blended benchmarks** must be identified as a *custom benchmark*. For a
  portfolio-weighted custom benchmark, GIPS provision 4.C.34 requires disclosure of the
  component benchmarks and their weights as of the most recent annual period end.

Source: <https://www.gipsstandards.org/wp-content/uploads/2023/08/gs_benchmarks_firms.pdf>

Nothing here is legal, tax or compliance advice, and GIPS is a **voluntary** standard that
firms opt into, not a regulation.

Two mandate-level conventions worth stating explicitly because they are routinely quoted as
if they were rules:

| Heuristic | Status |
|---|---|
| Market-neutral beta within $\pm 0.1$ | A **fund-mandate convention**, not a limit imposed by any regulator or standard-setter. The actual limit is whatever the offering documents specify. |
| IR "good" $> 0.5$, "excellent" $> 1.0$ | A widely repeated industry rule of thumb with no standard-setter behind it. Goodwin (1998) reports empirical IR distributions **by investment style**; style-specific percentiles are a better comparator than a universal cutoff. |

## Multi-strategy comparison

`compare_strategies` produces one row per strategy against a **shared** benchmark. The rows
are comparable only because four things are held fixed across them: the benchmark series,
the observation window, the `annualization_factor` and the `risk_free_rate`. Rows produced
by separate calls are not comparable unless all four match, and no engine can detect that
they do not — that is a reporting discipline, not a validation.

The same arithmetic answers the capital-allocation question when the benchmark is chosen to
be the **simple alternative** the book is supposed to beat: a static 60/40 blend, an
equal-weight sleeve of the same strategies, or cash. A multi-strategy portfolio marketed as
uncorrelated absolute return but showing a materially positive beta to a broad index is
carrying hidden beta, and its "alpha" is then measured against the wrong yardstick.

Each row is still **single-factor** CAPM. A comparison table does not become a factor model
by having more rows in it.

## Single-period only

Brinson allocation, selection, and interaction effects are additive **across sectors
within one period** and are not additive **across periods**: returns compound
multiplicatively while arithmetic effects add. Chaining requires a linking method —
Cariño (logarithmic smoothing), Menchero (optimized linking coefficients), Frongello,
or GRAP. This engine implements neither linking nor multi-period aggregation, by design.

The single-period identity $\sum_i (A_i + S_i + I_i) = R_p - R_b$ holds only when the
sector partition is mutually exclusive and exhaustive and both weight vectors sum to
1.0. The engine enforces both and asserts the identity before returning.

## Brinson-Fachler vs Brinson-Hood-Beebower

Both variants share the selection and interaction terms and both reproduce total active
return. They differ only in the allocation term:

- **BHB**: $A_i = (w_{p,i} - w_{b,i}) \cdot R_{b,i}$ — any overweight in a sector with a
  positive return scores positively, even if that sector lagged the benchmark.
- **BF** (implemented here): $A_i = (w_{p,i} - w_{b,i}) \cdot (R_{b,i} - R_b)$ — an
  overweight scores positively only if the sector beat the benchmark overall.

The two can differ in sign for the same position, which is why the variant must be
disclosed.

## Regulatory & operational notes

**The GIPS standards do not require performance attribution.** Attribution presented in a
GIPS Report is permitted as *supplemental information* and must be labelled as such; a
firm may present attribution for the composite treated as a single portfolio or for a
representative portfolio, with disclosure of how that portfolio was selected. Nothing in
this skill establishes GIPS compliance, which is a firm-level claim covering composite
construction, valuation, and reporting far beyond attribution arithmetic.

This skill makes no claim about SEC Marketing Rule, MiFID II, or any other jurisdiction's
performance-advertising requirements. Confirm those separately before using attribution
output in client-facing material.

## Sources

- Brinson-Fachler vs Brinson-Hood-Beebower allocation formulas and their sign behaviour —
  TSG Performance, "A great example of why BF is better than BHB",
  <https://tsgperformance.com/a-great-example-of-why-bf-is-better-than-bhb/>
- Attribution's status under GIPS (not required; supplemental information if presented) —
  TSG Performance, "Attribution and GIPS", <https://tsgperformance.com/attribution-and-gips/>;
  GIPS Standards for Firms (2020), <https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf>
- Multi-period linking of arithmetic attribution (Cariño, Menchero, Frongello, GRAP) —
  Frongello, "Linking Single Period Attribution Results",
  <https://frongello.com/support/Works/Chap20RiskBook.pdf>
- Information ratio definition, its relationship to a $t$-statistic, and annualization
  methods — Goodwin, T. H. (1998), "The Information Ratio", *Financial Analysts Journal*
  54(4), 34–43, <https://www.tandfonline.com/doi/abs/10.2469/faj.v54.n4.2196>
- Grinold & Kahn information-ratio percentile levels (0.5 good / 0.75 very good /
  1.0 exceptional; 75th and 90th percentiles) — *Active Portfolio Management*, 2nd ed.,
  p. 114, as quoted in <https://eng.wealthfront.com/2011/01/26/quantifying-investing-skill-information/>
  (the primary text was not directly accessible; treat the exact wording as second-hand).
- Geometric alpha annualization in `empyrical`/`pyfolio` —
  <https://github.com/quantopian/empyrical/blob/master/empyrical/stats.py> (`alpha_aligned`).
