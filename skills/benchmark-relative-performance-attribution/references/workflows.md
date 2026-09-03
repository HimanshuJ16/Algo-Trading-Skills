# Deep Workflow Reference — benchmark-relative-performance-attribution

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full procedure

### 1. Align the series and fix the conventions

- Align $R_p$ and $R_b$ on an explicit date index **upstream**. `evaluate_alpha_beta`
  enforces equal length only; a one-period shift passes that check and corrupts every
  statistic downstream.
- Confirm the benchmark series is **total return**, not price-only.
- Set `annualization_factor` to the true data frequency (252 / 52 / 12 / 365). This
  parameter appears linearly in alpha and as $\sqrt{N}$ in $TE$ and $IR$.
- Set `risk_free_rate` as an annual decimal. It is converted per period by simple
  division, matching the arithmetic alpha convention.

### 2. Alpha and beta

$$\beta = \frac{\text{Cov}(R_p, R_b)}{\text{Var}(R_b)} \quad \text{(sample, ddof=1)}$$

$$\alpha_{\text{period}} = \left(\bar{R}_p - \tfrac{R_f}{N}\right) - \beta\left(\bar{R}_b - \tfrac{R_f}{N}\right), \qquad \alpha = \alpha_{\text{period}} \cdot N$$

Notes:

- Annualization is arithmetic. `empyrical`/`pyfolio` compound instead; see
  `references/standards.md` for the size of the gap.
- $\text{Var}(R_b) \le 10^{-12}$ raises `AttributionError`. Beta is unidentified against
  a constant benchmark and a substituted $\beta = 1.0$ would flow straight into a
  sign-off gate as if it had been measured.
- Non-finite values raise. A NaN would otherwise reach `is_alpha_positive`, which
  evaluates `nan > 0` as `False` — an undefined result presented as a legitimate fail.

### 3. Active return, tracking error, information ratio

$$D_t = R_{p,t} - R_{b,t}, \qquad TE = \text{Std}(D_t)\sqrt{N}, \qquad IR = \frac{\bar{D} \cdot N}{TE}$$

- $\text{Std}$ is the sample standard deviation (`ddof=1`).
- $TE \le 10^{-12}$ with non-zero active return returns $\pm\infty$, not `0.0`: a portfolio
  that beats its benchmark by a constant every period has taken no active risk, and
  scoring that as average is a reporting error, not a conservative choice.
- Active return is **not** alpha — it still carries the beta mismatch.

### 4. Significance

$$t = \frac{\sqrt{n}\,\bar{D}}{s_D} = IR \cdot \sqrt{T}, \qquad T = n/N \text{ years}$$

Reported as `information_ratio_t_stat`. Report it beside the IR. An IR of 0.5 measured
over one year of daily data gives $t \approx 0.5$; the same IR needs roughly 15 years to
reach $\lvert t \rvert \ge 1.96$. Assumes serially independent active returns.

The engine warns below 30 observations and refuses below 5. Both are numerical floors,
not statistical sufficiency.

### 4a. Correlation and the caveat list

$$\rho = \frac{\text{Cov}(R_p, R_b)}{s_p\,s_b}$$

- Reported as `correlation_to_benchmark`, clipped to $[-1, 1]$ against float error.
- If either series is constant to within $10^{-12}$, correlation is 0/0 and is reported
  as `nan` with a warning — never `0.0`. The tolerance is deliberately at the
  floating-point noise floor: a `variance > 1e-8` guard would misclassify a genuine cash
  or short-duration benchmark (daily $\sigma \approx 1$bp) as constant and report
  $\beta = 0$ next to a correlation of 1.0.
- `AttributionSummary.warnings` collects every caveat that applies — sub-one-year sample,
  thin sample, $\lvert t \rvert < 1.96$, undefined correlation, unbounded IR — so a report
  generator cannot quote a headline number without them. An empty list means none applied.

**Decision point — unfunded leverage looks like alpha.** A 2x unfunded replication of the
benchmark reports $\alpha = +R_f$ exactly, because Jensen's alpha charges the risk-free rate
against one unit of capital while the portfolio earns twice the benchmark's excess return.
Alpha collapses to zero only once the borrowing cost of the second unit is inside the
portfolio return series. Check beta before reading alpha as skill.

### 4b. Multi-strategy comparison

`compare_strategies(strategy_returns, benchmark_returns)` runs steps 2-4a for every strategy
against one benchmark and returns `StrategyComparisonRow` objects; `render_comparison_table`
formats them as fixed-width text with a caveat count per row.

- Rows are sorted by information ratio descending, undefined ratios last, ties broken by
  strategy name so the ordering is deterministic. `sort_by_information_ratio=False` keeps
  the mapping's insertion order.
- A strategy that fails validation raises with its name in the message rather than being
  dropped: a comparison table with a silently missing row is worse than no table.
- **Decision point — what makes the rows comparable.** One benchmark, one window, one
  `annualization_factor`, one `risk_free_rate`. Nothing in the engine can detect rows
  stitched in from a different period, so that discipline sits with the report author.
- **Decision point — choosing the shared benchmark.** For the capital-allocation question,
  benchmark the whole book against the *simple alternative it is meant to replace*: a static
  60/40 blend, an equal-weight sleeve of the same strategies, or cash. A multi-strategy book
  sold as uncorrelated absolute return that shows a material beta to a broad index is
  carrying hidden beta. For a market-neutral mandate use a zero-beta custom benchmark or
  cash, never a long-only index — the residual of a badly chosen factor is not skill.
- Each row remains single-factor CAPM. A table does not become a factor model by having more
  rows in it; multi-factor attribution with proper inference is
  `strategy-performance-attribution-vs-market-beta`.

### 5. Brinson-Fachler sector attribution (single period)

Inputs must be **start-of-period** weights over a mutually exclusive and exhaustive
sector partition; both weight vectors must sum to 1.0.

For each sector $i$:

- Allocation: $A_i = (w_{p,i} - w_{b,i})(R_{b,i} - R_b)$
- Selection: $S_i = w_{b,i}(R_{p,i} - R_{b,i})$
- Interaction: $I_i = (w_{p,i} - w_{b,i})(R_{p,i} - R_{b,i})$

$R_b$ is derived from `benchmark_weights` and `benchmark_sector_returns` when
`total_benchmark_return` is left as `None`. Supplying it explicitly is validated against
the derived value; a mismatch raises.

Off-benchmark sectors ($w_b = 0$) are assigned $R_{b,i} = 0$. That does not affect the
reconciliation — a zero benchmark weight cancels the term — but it does shift value
between the allocation and interaction effects, so state the convention when reporting.

### 6. Reconcile

$$\sum_i (A_i + S_i + I_i) = R_p - R_b$$

Asserted by the engine before returning. If you re-implement the formulas, assert it
yourself: a decomposition that does not reconcile is not an attribution.

### 7. Sign-off

Apply the gates in `references/standards.md`, and record the $t$-statistic and sample
length alongside any alpha or IR figure. A gate on a point estimate with $\lvert t \rvert < 1.96$
should be recorded as such rather than reported as a pass.

## Failure modes observed in production

- **Beta mistaken for alpha.** Bull-market gains attributed to strategy skill with no
  beta adjustment.
- **Multi-period Brinson effects summed.** Monthly allocation effects added to produce an
  "annual" figure that does not reconcile to the annual active return, because arithmetic
  effects do not compound. Requires a linking method.
- **End-of-period weights.** The Brinson model is defined on start-of-period weights;
  end-of-period weights embed the return being attributed.
- **Partial sector coverage.** Top-N sector tables whose weights sum to less than 1.0
  produce effects that silently fail to reconcile.
- **Inconsistent total benchmark return.** A compounded annual benchmark return passed
  into a single-period call, producing allocation effects that look reasonable and do not
  add up.
- **Un-synchronized dates.** Equal-length but date-shifted series.
- **Silent NaN.** A single missing observation propagating through covariance into every
  reported metric and into the sign-off flag.
- **Flat benchmark.** A cash or risk-free comparator yielding a fabricated $\beta = 1.0$.

## Production implementation reference

- Reference code: `scripts/attribution_engine.py`
  (`PerformanceAttributionEngine`, `AttributionSummary`, `BrinsonSectorResult`,
  `AttributionError`).
- Automated unit tests: `scripts/test_attribution_engine.py`.
- Run with `python -m unittest discover -s skills/benchmark-relative-performance-attribution/scripts`.
