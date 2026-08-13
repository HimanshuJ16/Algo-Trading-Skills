# Benchmarking Standards

> Nothing here is legal, tax, or compliance advice. GIPS is a **voluntary** standard, not a
> regulation; firms claiming GIPS compliance opt into it. Consult qualified professionals
> before relying on any of this for an external performance presentation.

## Metric conventions

| Metric | Convention | Basis |
|--------|------------|-------|
| Beta | $\beta = \mathrm{Cov}(R_p, R_b)/\mathrm{Var}(R_b)$, sample covariance with `ddof=1`. | Standard single-factor market-model regression slope. |
| Jensen's Alpha | $\alpha = (\bar{R}_p - R_f) - \beta(\bar{R}_b - R_f)$, annualized arithmetically ($\times 252$). | Jensen (1968), *The Performance of Mutual Funds in the Period 1945–1964*, Journal of Finance 23(2). Arithmetic annualization keeps the CAPM decomposition linear; it is **not** a CAGR. |
| Tracking Error | $\mathrm{Std}(R_p - R_b) \times \sqrt{252}$ for a daily series. | $\sqrt{T}$ scaling is only valid for **serially uncorrelated** active returns. Smoothed or illiquid marks are positively autocorrelated, so this understates true annual tracking error. |
| Information Ratio | Annualized active return / annualized tracking error. | Goodwin (1998), "The Information Ratio", *Financial Analysts Journal* 54(4), 34–43 — which explicitly compares **four** different annualization methods, so an IR is only comparable across managers when the convention is stated. |
| Statistical support | $t = \mathrm{IR}_{\text{ann}} \times \sqrt{\text{years}}$ under an i.i.d. assumption. | Follows directly from $t = \bar{d}/(s_d/\sqrt{n})$. Goodwin (1998) treats this IR–t-statistic relationship explicitly. $|t| > 2$ is the usual ~5% two-sided rule of thumb; it is a heuristic, not a standard. |

**Consequence of the t-relationship:** an annualized IR of 0.5 needs roughly 16 years of data to reach $|t| = 2$. Most live multi-strategy track records cannot statistically distinguish a good IR from zero. Report the t-statistic and the observation count alongside every IR.

## Interpretation thresholds — heuristics, not standards

| Heuristic | Status |
|---|---|
| IR "good" $> 0.5$, "excellent" $> 1.0$ | Widely repeated industry rule of thumb. It has **no** standard-setter behind it and was not verifiable against a primary source during this review. Treat as a rough orientation only, and never as a sign-off gate on its own. Goodwin (1998) reports empirical IR distributions by investment style — style-specific percentiles are a better comparator than a universal cutoff. |
| Market-neutral Beta within $\pm 0.1$ | A **fund-mandate convention**, not a rule imposed by any regulator or standard-setter. The actual limit is whatever the fund's own offering documents and risk mandate specify. Verify against the mandate rather than assuming this band. |

## Benchmark appropriateness (GIPS)

The CFA Institute *Guidance Statement on Benchmarks for Firms* (effective 1 April 2021) sets out
what makes a benchmark appropriate. A benchmark should be **specified in advance, relevant,
measurable, unambiguous, representative of current investment options, accountable, investable,
and complete**. Practical requirements relevant to this skill:

- **Total returns only.** All composite, pooled fund, and benchmark returns in GIPS Reports must be
  total returns (including income). Comparing a total-return portfolio to a price-only index
  manufactures Alpha roughly equal to the index dividend yield.
- **An appropriate benchmark must be selected where one is available**; where the firm determines no
  appropriate benchmark exists, it must disclose why none is presented.
- **Blended benchmarks** must be identified as a *custom benchmark*. For a portfolio-weighted custom
  benchmark, GIPS provision 4.C.34 requires disclosure of the component benchmarks and their
  respective weights as of the most recent annual period end.

Source: <https://www.gipsstandards.org/wp-content/uploads/2023/08/gs_benchmarks_firms.pdf>

## Degeneracy handling

| Condition | Correct treatment |
|---|---|
| Benchmark series constant | Beta is **not identified** by the data. Reporting `0.0` is a convention (defensible for a cash/riskless benchmark) and must be labelled as such, never presented as a measurement. |
| Either series constant | Correlation is **undefined** (0/0), not zero. |
| Active return constant (zero tracking error) | Information Ratio is **undefined/unbounded**, not zero. Reporting `0.0` makes a perfectly consistent outperformer indistinguishable from a manager with no skill. |
| Degeneracy tolerances | Must sit at the floating-point noise floor (daily $\sigma \lesssim 10^{-12}$). A tolerance set at a plausible financial magnitude — e.g. `variance > 1e-8` — misclassifies a genuine low-volatility benchmark (daily $\sigma \approx 1$bp) as constant. |
| NaN/Inf in either series | Reject. NaN propagates silently through covariance and mean, producing output that looks numeric but is meaningless. |
