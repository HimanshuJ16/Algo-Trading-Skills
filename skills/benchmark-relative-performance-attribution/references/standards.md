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
