---
name: value-at-risk-var-live-monitoring
description: >-
  Use when monitoring a live position book to compute Parametric VaR, Historical
  Simulation VaR and Conditional VaR (CVaR / Expected Shortfall) on the current
  holdings, and to veto new risk-increasing orders when a VaR limit is breached
  while still letting risk-reducing orders through
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- value-at-risk
- var-monitoring
- expected-shortfall
- cvar
- live-risk
- pre-trade-controls
brokers_frameworks:
- Variance-Covariance (delta-normal) VaR
- Historical Simulation VaR
- BCBS FRTB (MAR32 / MAR33)
- 12 CFR 217 Subpart F (US market risk rule)
- 17 CFR 240.15c3-5 (market access controls)
- Python standard library (statistics, dataclasses)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when a live engine holds open market positions and you need a **portfolio-level
loss estimate refreshed against current weights**, plus a pre-trade gate on it. A VaR
computed once during backtesting describes the book you *tested*, not the book you
*hold*: weights drift with every fill and with every price move, and volatility
regimes change underneath a static number. This skill recomputes all three measures —
Parametric (variance-covariance), Historical Simulation, and CVaR / Expected Shortfall —
from the live position vector on each risk cycle, and returns an approve/veto verdict
against a NAV-fraction limit (e.g. 5%).

It is a **measurement and veto** component. It never submits or cancels anything; the
caller enforces the verdict.

## When NOT to Use

- **As your only pre-trade control.** A VaR limit bounds *distributional* loss under
  the sampled regime. It does not bound leverage, margin adequacy, per-symbol size,
  order rate, realised drawdown, or a fat-tail event outside the sample. Compose it
  with the skills under Related Skills; 17 CFR 240.15c3-5 contemplates a control
  *suite*, not a single metric.
- **On books containing options or other convex payoffs.** Both branches here revalue
  linearly (delta-normal), so gamma and vega risk are simply absent from the number.
  Use a full-revaluation or Greeks-based measure for those.
- **On a sample too short to locate the quantile.** At 99% confidence you need at
  least 100 observations for the tail bucket to hold one, and BCBS MAR32.18 /
  12 CFR 217.205(b)(2) put the supervisory floor at one year (~250). Below the derived
  floor the module refuses rather than returning a confident-looking number; below one
  year it warns. A "99% VaR" from 30 bars is the worst of 30 bars.
- **As a regulatory capital calculation.** This is a 1-period measure at the frequency
  of the returns you supply. FRTB capitalises 97.5% Expected Shortfall with
  liquidity-horizon scaling (MAR33.3, MAR33.4); nothing here reproduces that.
- **To answer "would this order breach?"** The module measures the *current* book. To
  gate on the post-fill state, fold the prospective fill into `positions` first.

## Prerequisites

- A live position vector $Q = [q_1, \dots, q_m]$ (shorts as **negative quantities**,
  never negative prices) and current prices $P = [p_1, \dots, p_m]$ in the NAV currency.
- A return history per held symbol, **all series equal length and indexed to the same
  observation dates, oldest first**. Alignment is the caller's responsibility; ragged
  input is rejected, not truncated.
- Portfolio NAV > 0, and a 1-day VaR limit as a fraction of NAV (e.g. `0.05`).

## Workflow

1. **Value the book and derive signed weights.**
   $V_i = q_i \cdot p_i$, $w_i = V_i / \text{NAV}$. Weights are signed, so shorts net
   against longs and leverage is already in the number: `gross_exposure_pct` reports
   $\sum_i |w_i|$ so a 3x-levered book is visibly 3x. Every held symbol must have both
   a price and a return series — a missing one is a rejection, because an unpriced or
   unmodelled position still carries real exposure.

2. **Reconstruct the portfolio return series.**
   $R_{p,t} = \sum_i w_i R_{i,t}$ over the aligned window. If series lengths differ,
   **stop**: truncating to the shortest pairs an old observation of one series with a
   recent observation of another, and the resulting covariance — and therefore the VaR —
   is silently wrong with no symptom.

3. **Compute Parametric (variance-covariance) VaR.**
   $\text{VaR}_{\text{param}} = z_c \cdot \sigma_p - \mu_p$, with $z_c$ the exact
   standard-normal quantile (`statistics.NormalDist.inv_cdf`), not a three-entry lookup
   table. Set `subtract_mean_drift=False` for the drift-free $z_c \sigma_p$ convention.

4. **Compute Historical Simulation VaR and CVaR.**
   Sort worst-first; with $k = \lceil n(1-c) \rceil$, VaR is the $k$-th worst loss and
   CVaR is the mean of those $k$ worst. State the convention — the common alternatives
   differ by one observation at exactly the round sample sizes, and $k$ is reported as
   `tail_observations_used` so the estimate's thinness is visible.

5. **Enforce the breaker, and name what tripped it.**
   A measure breaches at $\ge$ limit. Record *which* measures breached
   (`breaching_measures`) and the binding value — a breach log that quotes the
   parametric figure when the historical measure tripped is an audit record that
   contradicts itself. CVaR is reported always but enters the verdict only when
   `cvar_limit_pct` is set.

6. **Veto risk-*increasing* orders only.**
   Pass `is_risk_reducing=True` for a close, a partial reduction or a hedge. A breaker
   that blocks every order blocks the trades that would cure the breach. The module
   cannot verify the claim — it logs it so the override stays auditable.

> Full step-by-step procedure: see `references/workflows.md`.
> Verified regulatory and estimator standards: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Front-truncating ragged return histories.** Taking `min(len(...))` and reading
  from index 0 of each series pairs a 2019 observation of a long series with a 2024
  observation of a short one. A 50/50 book of one asset at +2% and one at −2% daily has
  a true VaR of zero; front-truncation on a 220/120 split reports 1.69%. Nothing in a
  list of floats can detect this — align upstream or fail closed.
- **A z-score lookup table with a silent fallback.** `z_table.get(level, 2.326)`
  returns the 99% multiplier for a 99.9% monitor, **understating** VaR by 25%. The
  dangerous direction of a lookup miss is the one that reports less risk than exists.
- **Letting a NaN through.** `NaN >= limit` is `False`, so a single bad tick makes the
  breaker approve every order while reporting success. Reject non-finite input; do not
  let it reach the comparison.
- **Estimating a 99% quantile from a short sample.** With `int((1-c)·n)` as a 0-based
  index and n < 100, the "99% historical VaR" is the single worst observation, and CVaR
  is numerically identical to VaR — the expected-shortfall column is then decorative.
- **Blocking the exit.** A blanket `approved=False` on breach refuses the closing and
  hedging trades that would bring the book back inside the limit, converting a breach
  into a trapped position.
- **Reading VaR as a worst case.** It is a quantile: at 99% one-day, roughly 2–3
  exceedances per trading year are *expected*. Losses beyond it are what CVaR sizes.
  Validate the exceedance count separately (`real-time-var-backtesting-kupiec-test`).
- **Trusting Parametric VaR alone on a fat-tailed book.** Normality understates tails;
  the gap between the parametric and historical figures is itself the diagnostic.

## Verification

- Run `python -m unittest discover -s skills/value-at-risk-var-live-monitoring/scripts`
  and confirm all tests pass. Expected values there are hand-derived from constructed
  samples, not re-computed with the implementation's own formula.
- Confirm the estimator convention on a designed sample: 100 observations whose four
  worst are −10%, −8%, −6%, −4% must give $k=1$, historical VaR 10.00% and CVaR 10.00%
  at 99%; at 95% the same sample gives $k=5$, VaR 0.00% (the 5th worst is a gain) and
  CVaR 5.58%.
- Confirm breach attribution: a book with three −8% days in 250 breaches a 5% limit on
  the **historical** measure only (parametric ≈ 2.09%), and `breaching_measures` must
  read `("historical_var",)` with a binding value of 8.00%.
- Confirm the breaker fails closed: NaN price, NaN return, NaN NAV, non-positive price,
  a held symbol missing a price or a return series, and ragged series must each raise
  `VaRMonitorError` — never a bare `KeyError`, `AttributeError` or a silent number.
- Confirm `is_risk_reducing=True` approves through a live breach while
  `breach_reason` stays populated and `risk_reducing_override` is `True`.

## Related Skills

- `real-time-var-backtesting-kupiec-test` — validate the exceedance count this monitor's
  limit implies, before trusting the limit.
- `multi-currency-var-aggregation` — the same measures when positions span currencies
  and FX is a second risk factor.
- `correlation-aware-exposure-limits` — bounds concentration, which a VaR limit does not.
- `kill-switch-and-drawdown-circuit-breakers` — realised-loss breaker, complementary to
  this distributional one.
- `broker-account-margin-call-handling` — margin adequacy, which VaR does not measure.
