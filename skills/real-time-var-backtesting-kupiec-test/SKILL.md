---
name: real-time-var-backtesting-kupiec-test
description: >-
  Use when you already hold a counted VaR backtest pair of observations and exceptions
  and need a calibration verdict; the Kupiec proportion-of-failures likelihood-ratio
  test for unconditional coverage. Blind to breach clustering.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: kupiec-test, var-backtesting, proportion-of-failures, basel-traffic-light, likelihood-ratio, risk-governance
  brokers_frameworks: "BCBS bcbs22 Backtesting Framework (January 1996); Basel Framework MAR32 / MAR99; Kupiec (1995) POF Likelihood Ratio Test; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you have a completed VaR backtest window — $T$ observations and $x$ realised exceptions — and need to answer two separate questions about it:

1. **Is the model statistically miscalibrated?** The Kupiec Proportion-of-Failures (POF) likelihood-ratio test evaluates the *unconditional coverage* null $H_0$: the true exception probability equals $p = 1 - \alpha_{\text{VaR}}$ (e.g. $p = 0.01$ for $99\%$ VaR).
2. **What does the supervisor do about it?** The Basel traffic-light framework maps the exception count onto a green / amber / red zone and, at the published 250-day basis, a backtesting-dependent capital multiplier.

These are different tests with different tails and they legitimately disagree. Report both.

## When NOT to Use

- **To detect clustered breaches.** The POF statistic is a function of the *count* alone and is blind to ordering. Ten breaches spread evenly and ten breaches arriving in one week produce an identical statistic. Clustering violates the independence property and needs a separate test (Christoffersen 1998 Markov test, or Christoffersen–Pelletier 2004 duration test).
- **As the sole evidence that a model is sound.** The test has low power at regulatory sample sizes. Kupiec (1995) reports that at the one-year, 8-violation threshold, a model reporting a $3\%$ VaR while claiming $1\%$ is caught only ~$65\%$ of the time. A non-rejection at $T = 250$ is weak evidence, not a clean bill of health.
- **To count the exceptions.** This skill consumes an already-counted $(T, x)$ pair. Under MAR32.18 the count is the *greater* of the actual-P&L and hypothetical-P&L exception counts; producing that count is upstream work.
- **To size a capital add-on off a non-250-day window.** BCBS generalises the zone *boundaries* to other sample sizes but publishes no multiplier steps for them. The multiplier is withheld (`None`) rather than extrapolated.
- **On a window still filling up.** A real-time backtester accumulating its first weeks of data will produce a statistic, but the $\chi^2$ approximation and the test's power are both degraded below $T = 250$; the result is flagged, not silently returned as authoritative.

## Prerequisites

- Number of observations $T \ge 1$ (Basel expects ~250, the most recent twelve months).
- Number of VaR exceptions $x$, with $0 \le x \le T$, already reconciled against daily P&L.
- VaR coverage level, passed as `confidence_level` (0.99 for $99\%$ VaR $\Rightarrow p = 0.01$).
- Statistical significance level for rejection, passed as `alpha` (default 0.05). **`alpha` is the significance level, not the VaR confidence level** — passing 0.99 here rejects essentially every model.
- No third-party packages. The $\chi^2_1$ survival function and the binomial CDF are computed from the standard library.

## Workflow

1. **Validate the input pair before computing anything**:
   - $T < 1$, $x < 0$, or $x > T$ raises. **Decision point — an empty window is a data-pipeline failure, not a passing model.** Returning "accepted" for $T = 0$ turns a feed outage into a silent all-clear on a regulatory control; the caller must handle the exception and escalate, not treat it as a green light.

2. **Compute the Kupiec POF statistic** ($LR_{\text{POF}}$), in log space:
   $$LR_{\text{POF}} = -2 \ln \left[ \frac{(1-p)^{T-x} p^x}{\left(1 - \hat{\pi}\right)^{T-x} \hat{\pi}^x} \right], \qquad \hat{\pi} = \frac{x}{T}$$
   - The $x = 0$ and $x = T$ branches are the analytic limits, $-2T\ln(1-p)$ and $-2T\ln p$.
   - Evaluate the logs, not the powers: $p^x$ is already $\sim 10^{-50}$ at $x = 25$, $p = 0.01$.

3. **Convert to a p-value under $\chi^2_1$**:
   $$P(\chi^2_1 > s) = \operatorname{erfc}\!\left(\sqrt{s/2}\right)$$
   - **Decision point — this is the identity, not an approximation.** $\exp(-s/2)$ is the $\chi^2_{\mathbf{2}}$ survival function and returns $0.1465$ at the $5\%$ critical value $3.8415$ where the answer is $0.0500$. Using it inflates every p-value by roughly $3\times$ near the decision boundary and reports rejected models as borderline.

4. **Interpret the rejection, including its direction**:
   - $p\text{-value} < \alpha \Rightarrow$ reject $H_0$. **Decision point — the Kupiec test is two-sided.** A rejection at $x < Tp$ means the model is *too conservative* (overstating risk, over-consuming capital), not that it is underestimating risk. Check `breach_direction` before escalating; zero exceptions in 250 days rejects at $p = 0.0250$ and is a capital-efficiency finding, not a breach event.
   - $p\text{-value} \ge \alpha \Rightarrow$ fail to reject. This is not proof of adequacy — see *When NOT to Use*.

5. **Classify into the Basel supervisory zone** (one-sided, upper tail only):
   - Amber begins at the smallest $x$ with $P(X \le x) \ge 95\%$; red at the smallest $x$ with $P(X \le x) \ge 99.99\%$. At $T=250$, $p=0.01$ this reproduces the published boundaries: green $0$–$4$, amber $5$–$9$, red $10$ or more.
   - **Decision point — do not linearly rescale the exception count** to a 250-day equivalent ($x \times 250/T$). The binomial tail is not linear in sample size: at $T = 1000$ the correct amber boundary is $15$, not $20$.
   - Attach the MAR32.9 multiplier only on the published basis ($T = 250$, $99\%$ coverage); otherwise report `None`.

6. **Emit the structured `KupiecResult`** carrying both verdicts, the expected and observed rates, the exact cumulative probability, and an audit note.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Substituting an exact binomial test for the Kupiec test and calling it Kupiec.** They are different tests and disagree at regulatory sample sizes: at $T=250$, $x=6$ the two-sided exact binomial rejects ($p = 0.0412$) while Kupiec does not ($p = 0.0594$); at $T=250$, $x=0$ Kupiec rejects ($p = 0.0250$) while the binomial does not ($p = 0.1889$). If the audit report says "Kupiec POF", the number in it must be $LR_{\text{POF}}$.
- **Using $\exp(-s/2)$ as the $\chi^2_1$ p-value.** That is the two-degrees-of-freedom survival function. It is the single most common way this test is silently miscalibrated, and it fails *unsafely* — p-values come out too high, so miscalibrated models pass.
- **Reporting a rejection flag and a p-value derived from different rules.** Rejecting on $LR > 3.841459$ while printing $\exp(-LR/2)$ produces results like "rejected, $p = 0.064$" — self-contradictory on the face of the audit trail.
- **Reading a two-sided rejection as "risk understated".** Too *few* breaches rejects just as hard. Escalating a conservative model as a breach event wastes an incident response; ignoring it wastes capital.
- **Treating green zone as "Kupiec passed".** The zones are one-sided and only penalise excess breaches, so a wildly over-conservative model is always green. Green means "no supervisory add-on", not "calibrated".
- **Confusing the 1996 "yellow" zone with something distinct from MAR32's "amber".** They are the same zone, renamed. The 1996 table published *increments* to the scaling factor (0.40 … 1.00 on a base of 3); the in-force MAR32.9 table publishes *total* multipliers (1.70 … 2.00). Do not add one to the other.
- **Running the test on a window shorter than 250 without flagging it.** The $\chi^2$ distribution is asymptotic, and at $x = 0$ the statistic sits on the boundary of the parameter space where the nominal p-value is least reliable. Prefer the exact cumulative probability on short windows.
- **Passing the VaR confidence level into `alpha`.** `KupiecVaRBacktester(0.99, 0.99)` rejects almost everything.

## Verification

- `KupiecVaRBacktester(confidence_level=0.99)` with $T=250$, $x=4$ must give $LR_{\text{POF}} = 0.769$ and with $x=10$ must give $12.955$, reproducing the values published in Campbell (FEDS 2005-21, Sec. 3.1) to their printed precision (0.76 and 12.95).
- `binomial_cdf(x, 250, 0.01)` must reproduce all eleven rows of BCBS Table 2 to two decimals: $8.11\%$, $28.58\%$, $54.32\%$, $75.81\%$, $89.22\%$, $95.88\%$, $98.63\%$, $99.60\%$, $99.89\%$, $99.97\%$, $99.99\%$.
- `basel_zone_boundaries(250, 0.01)` must return `(5, 10)`; at $T = 1000$ it must return `(15, 24)`, not the linearly rescaled `(20, 40)`.
- `chi_square_1df_survival(3.841458820694124)` must equal $0.05$, and must **not** equal $\exp(-s/2) = 0.1465$.
- $T=250$, $x=6$ must be `is_rejected=False` with `basel_zone="amber"` — the two verdicts disagreeing is correct, not a bug.
- $T=250$, $x=0$ must be `is_rejected=True` with `breach_direction="over_estimating_risk"` and `basel_zone="green"`.
- Negative checks: $T \le 0$, $x < 0$, $x > T$, non-integer $T$ or $x$, `confidence_level` outside $(0,1)$, and `alpha` outside $(0,1)$ must each raise.
- Run `python -m unittest discover -s skills/real-time-var-backtesting-kupiec-test/scripts` and confirm 100% pass rate.

## Related Skills

- `risk-model-backtesting-against-realized-outcomes`
- `value-at-risk-var-live-monitoring`
- `multi-currency-var-aggregation`
- `regulatory-capital-requirement-tracking`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `margin-utilization-circuit-breaker`
