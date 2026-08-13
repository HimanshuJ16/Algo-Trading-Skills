# Standards — backtest-reporting-standardized-tearsheet

A tearsheet is only "standardized" if every convention is pinned down. The same return
series yields materially different Sharpe, Sortino and Calmar values under different
defensible choices, so each one this generator makes is recorded below.

## Metric Conventions

| Metric | Convention | Notes |
|---|---|---|
| Return input | Simple per-period returns, chronological | Equity compounds as $\prod(1+R_t)$; log returns are not accepted |
| Annualized Return | Geometric, $\text{equity}_T^{\,ppy/n}-1$ (CAGR) | |
| Annualized Volatility | $\text{std}(R,\ \text{ddof}=1)\times\sqrt{ppy}$ | Sample, not population |
| Sharpe | $(\bar{R}\cdot ppy - r_f)\ /\ \text{annualized std}$ | Arithmetic numerator, deliberately not CAGR |
| Sortino | Same numerator, over annualized target downside deviation | MAR $= r_f/ppy$ |
| Calmar | $\text{CAGR}/|\text{maxDD}|$ by default | Excess variant opt-in; report states which |
| Max Drawdown | Negative fraction, from an equity curve seeded at 1.0 | Never worse than $-1.0$ |
| Hit Rate | Fraction of **periods** with $R_t > 0$ | Flat periods count in the denominator, not as wins |
| Profit Factor | Gross gains / absolute gross losses | |

## Sharpe: Why the Numerator Is Correct as Written

Subtracting an annual $r_f$ from an annualized arithmetic mean is exactly equivalent to
annualizing the mean of the per-period excess returns:

$$\text{mean}(R_t - r_f/ppy)\cdot ppy \;=\; \text{mean}(R_t)\cdot ppy - r_f$$

and because $r_f/ppy$ is a constant, $\text{std}(R_t - r_f/ppy) = \text{std}(R_t)$. Both
routes give an identical Sharpe; this was verified numerically against an
excess-returns-first computation on a 756-period series.

The numerator is **arithmetic** while `Annualized Return` is **geometric**. That is
deliberate and conventional for Sharpe, but it means the Sharpe numerator and the
headline return figure are not the same quantity. Do not reconstruct one from the other.

## Sortino: The Divisor That Is Commonly Wrong

Target downside deviation is

$$\text{TDD} = \sqrt{\frac{1}{n}\sum_{t=1}^{n}\big[\min(0,\ R_t - \text{MAR})\big]^2}$$

The sum of squared shortfalls is divided by **$n$, the total number of periods**, not by
the count of below-target periods. Averaging over only the losing periods inflates the
ratio — by a factor of 2 in a series where a quarter of the periods are losses. This
implementation divides by $n$.

The standard reference for this point is Rollinger and Hoffman, *Sortino: A "Sharper"
Ratio* (Red Rock Capital), which exists specifically to correct the mistake.
**Sourcing note:** the CME-hosted copy of that paper timed out and then reset during this
review, so the convention was corroborated from multiple secondary descriptions rather
than read first-hand. The formula above is stated explicitly here so a reader can check
it against the paper directly. The implementation was already using this convention; it
was documented, not changed.

## Calmar: Two Conventions Exist

Terry W. Young introduced the ratio in *Futures* (October 1991) as compound annualized
return over maximum drawdown, conventionally measured over a trailing 36 months. Two
forms circulate today:

| Form | Expression |
|---|---|
| Default here, and the more common | $\text{CAGR}\ /\ |\text{maxDD}|$ |
| Excess-return variant, opt-in | $(\text{CAGR} - r_f)\ /\ |\text{maxDD}|$ |

They coincide when $r_f = 0$. Neither is wrong, but a tearsheet that does not say which
one it used cannot be compared against another shop's number. The report therefore
carries a `Calmar Convention` string. The default changed to the first form in v2.0.0;
previously the risk-free rate was always subtracted, without being documented.

Note also that this generator computes Calmar over the whole supplied series, not over a
trailing 36-month window. Slice the input if you need the conventional window.

## Drawdown

The equity curve is prefixed with 1.0 before the running maximum is taken:

$$\text{equity} = [1.0,\ \textstyle\prod_{t\le 1}(1+R_t),\ \ldots],\qquad
\text{dd}_t = \frac{\text{equity}_t}{\max_{s\le t}\text{equity}_s} - 1$$

Without the seed the running maximum begins at the first period's close, and any decline
starting on period one is invisible. Seeding also guarantees the running maximum is at
least 1.0, so the denominator can be neither zero nor negative and the drawdown is
bounded in $(-1, 0]$.

`Max Drawdown Duration` is periods from the prior peak to the trough.
`Max Drawdown Recovery Periods` is periods from the trough until equity first regains the
prior peak, or `None` if it never does within the sample.

## Degenerate Denominators

A zero denominator returns $+\infty$, $-\infty$ or `nan` — never `0.0`, which would
present a degenerate calculation as poor performance.

| Situation | Result |
|---|---|
| Zero volatility, positive excess return | $+\infty$ Sharpe |
| Zero volatility, negative excess return | $-\infty$ Sharpe |
| Zero volatility, zero excess return | `nan` |
| Zero maximum drawdown, positive CAGR | $+\infty$ Calmar |
| No losses, positive gains | $+\infty$ profit factor |
| Neither gains nor losses | `nan` profit factor |

Constancy is tested exactly on the input rather than by comparing the derived standard
deviation against a tolerance: `numpy.std` of a constant series returns accumulated
rounding error of order $10^{-18}$, which would otherwise turn an unbounded Sharpe into a
finite but absurd $\approx 8.7\times10^{16}$.

## Rejected Inputs

Rejected with `TearsheetError` rather than silently producing a report:

- non-finite values — a NaN leaves the count-based statistics plausible while every risk
  statistic becomes `nan`, yielding a half-valid tearsheet;
- any return below $-1.0$, which drives equity negative, where CAGR and drawdown are
  undefined (exactly $-1.0$ is accepted and zeroes the curve permanently);
- fewer than 2 observations, below which a sample standard deviation does not exist and
  annualization is meaningless;
- non-1-D input, scalars, and non-numeric data.

An empty array returns `{}` rather than raising, preserving the original contract.

## Regulatory Scope — US Advisers Only

Backtested results are **hypothetical performance** under the SEC Marketing Rule:
17 CFR § 275.206(4)-1(e)(8) defines hypothetical performance as "performance results that
were not actually achieved by any portfolio of the investment adviser", and the
definition expressly covers "[p]erformance that is backtested by the application of a
strategy to data from prior time periods when the strategy was not actually used during
those time periods".

Where an SEC-registered investment adviser includes such performance in an advertisement,
§ 275.206(4)-1(d)(6) requires the adviser to adopt and implement policies and procedures
reasonably designed to ensure the hypothetical performance is relevant to the likely
financial situation and investment objectives of the intended audience, and to provide
sufficient information for that audience to understand the criteria and assumptions used
and the risks and limitations of relying on it.

Applicability: this is a **US investment-adviser advertising rule**. It does not govern
internal research use of a tearsheet, and it is not a requirement on the arithmetic in
this module. Other jurisdictions impose their own requirements; verify before
distributing. Nothing in this file is legal advice.

## Sources

- SEC Marketing Rule, 17 CFR § 275.206(4)-1 (definitions at (e)(8), hypothetical
  performance conditions at (d)(6)), via the Cornell LII e-CFR reproduction —
  <https://www.law.cornell.edu/cfr/text/17/275.206(4)-1>
- Rollinger, T. and Hoffman, S., *Sortino: A "Sharper" Ratio*, Red Rock Capital —
  <https://www.cmegroup.com/education/files/rr-sortino-a-sharper-ratio.pdf>
  (see sourcing note above: not retrievable during this review)
- Young, T. W., "Calmar Ratio: A Smoother Tool", *Futures*, October 1991 — the original
  print article is not available online; the definition above reflects the convention as
  consistently reported by secondary sources.

## Category

`backtesting-methodology`
