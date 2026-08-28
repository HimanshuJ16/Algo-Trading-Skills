---
name: risk-model-backtesting-against-realized-outcomes
description: >-
  Use when you hold a dated series of daily realized P&L against forecast VaR (and
  optionally Expected Shortfall) and need to turn it into an auditable model-validation
  verdict: count exceptions under the Basel missing-data and actual-vs-hypothetical rules,
  run Kupiec's POF test for unconditional coverage and Christoffersen's Markov test for
  breach clustering, and assign the Basel supervisory traffic-light zone with its published
  capital multipliers.
domain: Risk Management & Quantitative Auditing
subdomain: VaR Backtesting & Model Validation
tags: ["var-backtesting", "kupiec-pof-test", "christoffersen-independence", "basel-traffic-light", "expected-shortfall", "risk-model-validation", "cvar"]
brokers_frameworks: ["BCBS bcbs22 Backtesting Framework (January 1996)", "Basel Framework MAR32", "SEC 17 CFR 240.15c3-1e Appendix E", "Kupiec (1995) POF Test", "Christoffersen (1998) Markov Test", "Acerbi-Szekely (2014) ES Backtest"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you have the **raw daily record** of a VaR model — date, realized P&L,
forecast VaR — and need to convert it into a defensible model-validation verdict. This is
the *observation-level* layer: it owns exception counting, the ordering-sensitive tests,
and the audit trail.

Three assessments come out of the same window, and they answer different questions:

| Assessment | Question | Sided | Distribution |
|---|---|---|---|
| Kupiec POF | Is the exception *rate* right? | Two-sided | $\chi^2_1$ |
| Christoffersen Markov | Are exceptions *clustered*? | Two-sided | $\chi^2_1$ |
| Basel traffic light | What does the *supervisor* do? | One-sided (upper tail) | Binomial |

Report all three. They legitimately disagree: at $T = 250$, $x = 6$ Kupiec does not reject
($p = 0.0594$) while the Basel zone is already yellow.

Who mandates this, and where — do not universalize these:

- **BCBS-supervised banks on the internal models approach.** MAR32.5 requires bank-wide
  backtesting of one-day 99% VaR; MAR32.18 adds trading-desk backtesting at both 97.5% and
  99%. MAR32.3(3) bases the supervisory response on exceptions over 12 months (250 trading
  days).
- **SEC alternative-net-capital broker-dealers** approved under Appendix E to Rule 15c3-1.
  17 CFR 240.15c3-1e(d)(1)(iii) requires backtesting at a 99%, one-tailed, one-business-day
  measure over "the past 250 business days", with the exception count identified "on the
  last business day of each quarter".

The daily *comparison* is continuous; the formal *accounting of exceptions* is quarterly in
both regimes.

## When NOT to Use

- **As a substitute for the (T, x) statistical layer** when you already hold a counted
  pair. `real-time-var-backtesting-kupiec-test` consumes $(T, x)$ directly and carries the
  breach-direction reporting; this skill exists to *produce* that count correctly.
- **To validate a model on a series that is not in chronological order, or that contains
  duplicate dates.** The independence test reads the ordering of the hit sequence. This
  skill raises rather than returning a meaningless verdict.
- **To size a capital add-on off a non-250-day window.** BCBS generalises the zone
  *boundaries* to other sample sizes but publishes no multiplier steps for them. The three
  multiplier tables are withheld (`None`) off the published basis rather than extrapolated.
- **To decide whether a supervisor will disallow a model.** A red zone triggers an
  automatic multiplier increase and *may* lead to disallowance (MAR32.15). That is a
  supervisory determination; this tool reports the zone, not the outcome of a supervisory
  dialogue.
- **As a significance test on Expected Shortfall.** The Acerbi–Székely $Z_2$ statistic is
  reported as a signed diagnostic with **no p-value**, because its critical value requires
  simulating the predictive distribution, which this skill does not have.
- **For the FRTB desk-level pass/fail gate.** MAR32.19 sends a desk to the standardised
  approach at more than 12 exceptions at 99% or 30 at 97.5% — a different rule from the
  bank-wide traffic light, and out of scope here.

## Prerequisites

- A list of `DailyRiskObservation`: `date_iso` (strict ISO 8601, unique, strictly
  increasing), `realized_pnl_usd` (negative for a loss), `forecast_var_usd` (a **positive**
  magnitude), `confidence_level` (must match the engine's).
- Optional `hypothetical_pnl_usd` — the P&L had end-of-day positions been held unchanged.
  Supply it on **every** observation to activate the MAR32.5(1) greater-of rule.
- Optional `forecast_es_usd` — a positive ES magnitude at the same coverage level, on every
  observation, to activate the $Z_2$ diagnostic.
- At least `MINIMUM_OBSERVATIONS` (20) days. This is a usability floor, not a regulatory
  one; the regulatory basis is 250 trading days and anything short of it is flagged.
- `significance_level` is the **statistical** significance level (default 0.05), not the
  VaR confidence level. Passing 0.99 there rejects essentially every model.
- No third-party packages.

## Workflow

1. **Validate the window before computing anything.** Malformed or out-of-order dates, a
   non-positive VaR forecast, a per-observation `confidence_level` that disagrees with the
   engine's, or a wrong field type each raise.
   - **Decision point — a broken window is a pipeline failure, not a passing model.**
     Returning GREEN on an unusable series turns a feed outage into a silent all-clear on a
     regulatory control. Escalate the exception; do not treat it as a green light.
   - **Decision point — a non-positive VaR is rejected, not absolute-valued.** A zero or
     negative forecast means the feed is broken. Silently treating it as a limit of zero
     would flag every losing day as an exception.

2. **Build the hit sequence.** An exception is $\text{PnL} < -\text{VaR}$, strictly: a loss
   exactly equal to the forecast is covered.
   - **Decision point — missing data counts as an exception, not a skipped day.** MAR32.5(2):
     "In the event either the P&L or the daily VaR measure is not available or impossible to
     compute, it will count as an outlier." A NaN P&L that compares false and vanishes from
     the count reports a broken feed as a clean backtest.

3. **Apply the actual-vs-hypothetical rule if both series are present.** MAR32.5(1):
   exceptions are counted separately on each basis and "the overall number of exceptions is
   the greater of these two amounts". A partial hypothetical series is ignored with a
   warning rather than mixed into the actual one.

4. **Kupiec POF likelihood-ratio test** of unconditional coverage, in log space:
   $$LR_{\text{POF}} = -2 \ln \left[ \frac{(1-p)^{T-x} p^x}{(1 - \hat{\pi})^{T-x} \hat{\pi}^x} \right], \qquad \hat{\pi} = \frac{x}{T}$$
   - Convert under $\chi^2_1$: $P(\chi^2_1 > s) = \operatorname{erfc}(\sqrt{s/2})$. This is
     the identity, not an approximation. $\exp(-s/2)$ is the $\chi^2_{\mathbf{2}}$ survival
     function and returns $0.1465$ at the 5% critical value $3.8415$ where the answer is
     $0.0500$ — it inflates every p-value roughly threefold and fails *unsafely*.

5. **Christoffersen Markov test** of independence on the same hit sequence:
   $$LR_{\text{ind}} = -2 \ln \left[ \frac{(1-\pi)^{n_{00}+n_{10}} \pi^{n_{01}+n_{11}}}{(1-\pi_{01})^{n_{00}} \pi_{01}^{n_{01}} (1-\pi_{11})^{n_{10}} \pi_{11}^{n_{11}}} \right] \sim \chi^2_1$$
   with $\pi_{01} = n_{01}/(n_{00}+n_{01})$, $\pi_{11} = n_{11}/(n_{10}+n_{11})$. The joint
   conditional-coverage statistic is $LR_{cc} = LR_{uc} + LR_{ind} \sim \chi^2_{\mathbf{2}}$
   — note the **two** degrees of freedom.
   - **Decision point — this is the test Kupiec cannot do.** Ten breaches in one week and
     ten spread evenly give an *identical* POF statistic and an identical Basel zone.
     Clustering signals a model that reacts too slowly to changing conditions, and a run of
     consecutive breaches can be harder to survive than the same number spread out.

6. **Assign the Basel zone from the binomial rule, never by rescaling.**
   - The yellow zone begins at the smallest $x$ with $P(X \le x) \ge 95\%$; red at the
     smallest $x$ with $P(X \le x) \ge 99.99\%$. At $T=250$, $p=0.01$ this reproduces the
     published green $0$–$4$ / yellow $5$–$9$ / red $10+$.
   - **Decision point — do not linearly rescale the exception count** to a 250-day
     equivalent ($x \times 250/T$). The binomial tail is not linear in sample size, and
     rescaling fails in both directions: at $T=1000$ the correct red boundary is $24$, not
     $40$, so 30 exceptions in 1000 days would be reported green; at $T=25$ a single
     exception would be rescaled to 10 and rejected as red.

7. **Attach the capital consequence, only on the published basis** ($T = 250$, 99%
   coverage). Three distinct tables are reported and **must not be added to each other**:
   bcbs22 Table 2 publishes *increments* on a base of 3; MAR32.9 Table 1 publishes *total*
   multipliers on a different base; SEC Appendix E Table 1 publishes total factors on a
   base of 3.

8. **Optionally compute the ES diagnostic** and emit the structured
   `RiskModelBacktestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rescaling the exception count to a 250-day equivalent.** bcbs22 Table 2 says to deduce
  the boundaries from binomial probabilities at the actual sample size, not to rescale the
  count. Rescaling reports a badly miscalibrated long window as green and rejects a
  perfectly ordinary short one as red.
- **Letting a NaN P&L vanish.** `nan < -var` is `False` in IEEE 754, so a missing value
  silently becomes a non-exception. MAR32.5(2) says the opposite: it counts as an outlier.
  This is the failure mode where a dead feed produces a validated model.
- **Using $\exp(-s/2)$ as the $\chi^2_1$ p-value.** That is the two-degrees-of-freedom
  survival function. It fails unsafely — p-values come out too high, so miscalibrated
  models pass. Use it *only* for $LR_{cc}$, which genuinely has 2 df.
- **Reading "red zone" as "model disqualified".** bcbs22 Sec. III(f) directs the supervisor
  to "automatically increase the multiplication factor applicable to a firm's model by one
  (from three to four)"; MAR32.15 adds that the supervisor may disallow the model.
  Disallowance is a supervisory decision, not an automatic output of a backtest.
- **Adding the bcbs22 increments to the MAR32.9 multipliers.** The 1996 table publishes
  increases to a scaling factor of 3 (0.40 … 1.00); the in-force MAR32.9 table publishes
  total multipliers on a different base (1.70 … 2.00). SEC Appendix E happens to equal
  $3 + \text{bcbs22 increment}$; MAR32.9 does not.
- **Treating green zone as "the model is calibrated".** The zones are one-sided and only
  penalise excess breaches, so a wildly over-conservative model is always green. Kupiec is
  two-sided and *will* reject it — zero exceptions in 250 days rejects at $p = 0.0250$,
  which is a capital-efficiency finding, not a breach event.
- **Passing a mixed-coverage window.** Feeding 95% VaR forecasts to a 99% engine makes the
  exception count uninterpretable. This skill raises on a `confidence_level` mismatch
  rather than silently using the engine's own level.
- **Backtesting on actual P&L alone when hypothetical P&L exists.** MAR32.5(1) takes the
  *greater* of the two counts. Reporting only the actual count understates exceptions
  whenever intraday trading or fee income masked a breach in the static portfolio.
- **Rounding statistics at the API boundary.** A decisive rejection printed as
  "p-val = 0.0" is not an audit trail. Full precision is returned; format at the edge.

## Verification

- `binomial_cdf(k, 250, 0.01)` must reproduce all eleven rows of bcbs22 Table 2 to two
  decimals: 8.11%, 28.58%, 54.32%, 75.81%, 89.22%, 95.88%, 98.63%, 99.60%, 99.89%, 99.97%,
  99.99%.
- `basel_zone_boundaries(250, 0.01)` must return `(5, 10)`; at $T = 1000$ it must return
  `(15, 24)`, **not** the linearly rescaled `(20, 40)`; at $T = 500$, `(9, 15)`.
- `chi_square_1df_survival(3.841458820694124)` must equal $0.05$ and must **not** equal
  $\exp(-s/2) = 0.1465$; `chi_square_2df_survival(5.991464547107979)` must equal $0.05$.
- 250 observations with 2 exceptions $\implies$ `GREEN`, $LR_{\text{POF}} < 3.841$, accepted.
  With 12 exceptions $\implies$ `RED`, $LR_{\text{POF}} > 3.841$, not accepted.
- 1 exception in 25 days must **not** be `RED`; 30 exceptions in 1000 days **must** be `RED`.
- A window of 250 clean days with one NaN `realized_pnl_usd` must report exactly 1 exception
  flagged `is_missing_data_outlier`; 20 NaN days must reach the `RED` zone.
- Eight clustered breaches and eight evenly spread breaches must give an identical Kupiec
  statistic and an identical Basel zone, but only the clustered window may set
  `exceptions_are_clustered`.
- `christoffersen_cc_lr_stat` must equal `kupiec_lr_stat + christoffersen_ind_lr_stat`.
- Negative checks: malformed, duplicate or out-of-order dates; a non-positive VaR or ES; a
  `confidence_level` mismatch; a non-numeric field; `confidence_level` outside $(0,1)$ —
  each must raise.
- Run `python -m unittest discover -s skills/risk-model-backtesting-against-realized-outcomes/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `real-time-var-backtesting-kupiec-test`
- `value-at-risk-var-live-monitoring`
- `risk-limit-calibration-against-historical-drawdowns`
- `risk-metric-recalculation-frequency-tuning`
- `regulatory-capital-requirement-tracking`
- `backtest-audit-trail-for-regulatory-review`
