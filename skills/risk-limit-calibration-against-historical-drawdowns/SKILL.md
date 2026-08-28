---
name: risk-limit-calibration-against-historical-drawdowns
description: >-
  Use when setting or recalibrating a strategy's maximum-drawdown limit, daily loss
  limit and position-size scalar from its own realized daily return history, using
  observed max drawdown, Ulcer Index, historical VaR/Expected Shortfall, and a
  peaks-over-threshold generalized Pareto tail fit.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- drawdown-calibration
- var-cvar
- ulcer-index
- extreme-value-theory
- position-sizing
brokers_frameworks:
- Peaks-Over-Threshold GPD (Extreme Value Theory)
- Historical Simulation VaR/ES
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a strategy needs a maximum-drawdown limit, a daily loss limit and a position-size scalar, and the alternative is picking a round number. A limit set too tight halts the strategy on ordinary noise; a limit set too loose never fires before the capital is gone. This engine derives all three from the strategy's own realized daily returns: observed peak-to-trough drawdown and its duration, the Ulcer Index, historical VaR/Expected Shortfall at a configurable confidence, and — where the history supports it — a peaks-over-threshold generalized Pareto fit of the loss tail.

**Every number this skill produces is your own risk policy.** Nothing surveyed in `references/standards.md` sets a drawdown or daily-loss figure for a trading firm. MiFID II RTS 6 Art. 15(4) requires an investment firm to *set* market and credit risk limits from its own capital base, clearing arrangements, strategy and risk tolerance — it prescribes the obligation to calibrate, not the result. Never present these outputs to an auditor as regulatory minimums.

## When NOT to Use

- **As the enforcement mechanism.** This is an offline calibration step that returns numbers. Runtime enforcement must be an independent control outside strategy logic — `kill-switch-and-drawdown-circuit-breakers` and `portfolio-level-stop-loss-independent-of-strategy-stops`.
- **On a history shorter than the calibration window.** The engine requires 252 observations by default and refuses below 126 (~6 months) whatever you pass. A 99% tail needs at least 100 observations to contain a single loss at all; below that the "99% VaR" is the worst day in a short sample wearing a confidence level it has not earned.
- **To learn about a loss the strategy has never taken.** `HISTORICAL_MAX_DD` is bounded above by the worst outcome already in the sample. `EXTREME_VALUE_THEORY` extrapolates past it, but only in the shape the fitted tail implies — neither invents a gap, a limit-down or a liquidity crunch. Pair with `stress-testing-against-historical-crash-scenarios` and `scenario-based-stress-testing-custom-shocks`.
- **To get a distribution of drawdowns.** Resampling the return series to obtain the 95th-percentile max drawdown across simulated paths is `monte-carlo-strategy-robustness-testing`. This engine reads one realized path.
- **On returns that are not fractional returns on account equity.** `0.02` means +2%. Currency P&L compounded as a return produces meaningless drawdowns; a return at or below `-1.0` is rejected rather than compounded through zero.
- **On serially dependent or volatility-clustered returns, for the horizon-scaled methods.** `PARAMETRIC_VAR` and `EXTREME_VALUE_THEORY` convert a per-period figure to `horizon_days` under an IID assumption. `HISTORICAL_MAX_DD` carries no such assumption and is the safer default when that assumption is doubtful.

## Prerequisites

- Daily fractional returns on account equity, chronologically ordered, ending at the last completed session, with no gaps. At least `min_observations` (default 252); the hard floor is 126.
- Portfolio capital in USD, finite and strictly positive.
- A calibration window that includes at least one adverse regime. Basel's analogue for banks is instructive: the stressed ES observation horizon "must, at a minimum, span back to and include 2007" (MAR33.6). Calibrating on a benign year produces a limit that is only valid in a benign year.
- Policy choices, all of which default but none of which are mandated: `stress_buffer_multiplier` (>= 1.0), `target_confidence_pct`, `horizon_days`, the drawdown-limit floor and cap, the daily-loss VaR multiple, and the position-scalar threshold.

## Workflow

1. **Validate the return series before measuring anything.**
   - Non-finite returns and returns `<= -1.0` are rejected, not cleaned. **Decision point:** a single `NaN` reaching the daily loss limit makes that limit `NaN`, and `loss > NaN` is `False` for every loss — the control can never fire. Resolve the gap in the data; do not calibrate around it.

2. **Compute realized drawdown metrics.**
   - Equity curve, peak-to-trough max drawdown, longest run strictly below the running peak, Ulcer Index (Martin & McCann: the root mean square of percentage drawdowns from the running peak), daily mean and volatility.
   - **Decision point — if `drawdown_unrecovered` is true**, the series ends below its peak and the duration is right-censored. The true recovery time is not yet observable, so do not report the duration as a recovery time.

3. **Compute historical VaR and Expected Shortfall at `target_confidence_pct`.**
   - Order statistics: with `n` observations and confidence `q`, `k = ceil((1-q)·n)`; VaR is the `k`-th smallest return negated, ES the negated mean of the `k` smallest. ES ≥ VaR by construction.
   - **Decision point — if VaR is zero**, the sample contains no loss at that confidence. The engine raises rather than issuing a `$0` daily loss limit. Extend the window to include a losing regime.

4. **Pick a calibration method deliberately — they do not measure the same thing.**
   - `HISTORICAL_MAX_DD`: observed max drawdown × stress buffer. The only method that measures an actual drawdown. Bounded by the sample.
   - `PARAMETRIC_VAR`: the `h`-day cumulative loss quantile under IID normal, $\text{loss}_h = -h\mu + z_q\sigma\sqrt{h}$. **Drift scales with $h$, volatility with $\sqrt{h}$** — scaling a one-day VaR (which already embeds $-\mu$) by $\sqrt{h}$ mis-scales the drift. This is a fixed-window loss, and therefore a *lower bound* on the drawdown over a window of the same length: a drawdown maximises over every start point inside the window.
   - `EXTREME_VALUE_THEORY`: peaks-over-threshold GPD fit of the loss tail, giving a per-day tail VaR/ES, scaled to `horizon_days` by $\sqrt{h}$. **Decision point:** the fit raises rather than degrading when the tail has too few exceedances, is degenerate, or when the requested confidence sits below the fitted threshold. A raise is the correct answer; a fallback to another method would put a number in the audit record that the named method did not produce.

5. **Apply the policy floor and cap, and record which one bound.**
   - The limit is clipped into `[drawdown_limit_floor_pct, drawdown_limit_cap_pct]` for **every** method. **Decision point — if `floor_binding` or `cap_binding` is true**, the limit was set by policy, not by the return sample. Do not report it as a measurement of the strategy's risk.

6. **Derive the daily loss limit and position scalar, then hand the record to review.**
   - Daily loss limit = capital × historical VaR × `daily_loss_var_multiple`, computed from the unrounded VaR.
   - Position scalar = `position_scalar_threshold_pct / observed max drawdown` when the observed drawdown exceeds the threshold, otherwise `1.0`.
   - `CalibratedRiskLimits` carries `metrics`, `tail_fit`, `limit_basis`, the binding flags and `audit_notes`, so the record states what the number is and how it was produced.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the three methods as interchangeable estimates of one quantity.** A daily VaR is not a drawdown. Multiplying a one-day tail loss by an unexplained constant does not turn it into a drawdown limit; the horizon and the scaling rule must be stated, and `limit_basis` states them.
- **Scaling a VaR that contains a drift term by $\sqrt{h}$.** Over $h$ days the drift contributes $h\mu$, not $\sqrt{h}\mu$. On a strategy with meaningful positive drift the two differ by several percentage points of drawdown limit — in the direction of an over-generous limit.
- **Calling a magic multiplier "EVT".** Extreme Value Theory means fitting a tail distribution and extrapolating from the fit, with a threshold, an exceedance count and shape/scale parameters you can inspect. If those are not in the output, whatever produced the number was not EVT.
- **Reading a floored or capped limit as a calibration result.** A benign sample floored to 5% and a catastrophic sample capped at 50% both look like calibrated numbers and are not. Check `floor_binding` / `cap_binding`.
- **Reporting a censored drawdown duration as a recovery time.** A series that ends underwater has not recovered; its longest underwater run is a lower bound.
- **Setting a stress buffer below 1.0.** That sets a limit tighter than a loss the strategy has already survived and lived through, guaranteeing a halt the next time it repeats. The engine rejects it.
- **Calibrating a 99% tail on a short window.** With $n$ observations the 99% tail is the worst $\lceil 0.01n \rceil$ of them; at $n = 10$ that is one observation and the "confidence level" is decoration. The engine refuses below 126 observations and below the count needed to contain a single tail loss.
- **Trusting the GPD shape on a very heavy tail.** The method-of-moments estimator is structurally bounded above by $\xi = 0.5$, so it cannot represent an infinite-variance tail and will *understate* one. The module logs a warning at $\xi \ge 0.25$.
- **Feeding non-finite or ruinous returns.** A `NaN` produces a limit that no comparison ever breaches; a return below `-1.0` drives equity through zero and every subsequent drawdown figure is meaningless.

## Verification

- Instantiate `DrawdownLimitCalibratorEngine()` (defaults: 1.5× buffer, 99% confidence, 252-observation window, 20-day horizon, 5%/50% floor/cap).
- Feed a 252-observation series that peaks at 1.0, falls exactly 25%, recovers to a new high, then takes three −0.1% losses: verify `metrics.max_drawdown_pct == 25.0`, `calibrated_max_drawdown_pct == 37.5` (1.5 × 25), `position_size_scalar == 0.8` (20 / 25), and both binding flags false.
- Feed `[-0.10] + [0.0] × 251`: verify `ulcer_index == 10.0` exactly and `drawdown_unrecovered` is true.
- Feed `[0.002] × 249 + [-0.03, -0.04, -0.05]`: with `k = ceil(0.01 × 252) = 3`, verify `var_pct == 3.0` and `cvar_pct == 4.0`.
- Feed `[0.001 + 0.01, 0.001 - 0.01] × 126` with `PARAMETRIC_VAR`: $\mu = 0.001$, $\sigma = 0.01\sqrt{252/251}$, so the 20-day limit is $1.5 \times (-20\mu + z_{99}\sigma\sqrt{20}) = 12.6367\%$ — not the $14.9635\%$ produced by $\sqrt{h}$-scaling a one-day VaR.
- Verify the GPD fit against a hand-computed case: 246 gains, one loss at the threshold, five exceedances with excesses $0.001\ldots0.005$ gives mean $0.003$ and sample variance $2.5\times10^{-6}$, hence $\xi = -1.3$ and $\beta = 0.0069$ exactly, with POT VaR $= 1.312965\%$ and ES $= 1.436072\%$ at 99%.
- Negative checks: a `NaN` return, a return `<= -1.0`, a 10-observation series, non-positive or non-finite capital, a stress buffer below 1.0, a confidence level the window cannot support, a degenerate tail, and an EVT request with too few exceedances must each raise a `CalibrationError` subclass.
- Run `python -m unittest discover -s skills/risk-limit-calibration-against-historical-drawdowns/scripts` and confirm a 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `monte-carlo-strategy-robustness-testing`
- `risk-limit-breach-escalation-matrix`
- `risk-metric-recalculation-frequency-tuning`
- `stress-testing-against-historical-crash-scenarios`
- `value-at-risk-var-live-monitoring`
