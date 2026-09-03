---
name: scenario-based-stress-testing-custom-shocks
description: >-
  Use when revaluing a factor-mapped book under deterministic multi-factor shocks with
  betas and durations, whether a historical crisis or a hypothetical. Per-symbol replay
  of a past crash is stress-testing-against-historical-crash-scenarios.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, stress-testing, custom-shocks, factor-shocks, duration-shock, drawdown-breach, scenario-coverage
  brokers_frameworks: "Scenario Stress Testing Engine; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when the question is "what does this book lose if *this* happens?" and
the answer has to be reproducible, auditable and independent of whatever the recent
return distribution happens to look like. VaR and Expected Shortfall are calibrated to a
historical window; a deterministic scenario is not, which is the whole point of running
one alongside them.

The engine is **factor-based**: each position declares one risk factor and one
sensitivity to it, and each scenario declares a shock per factor. Two shock types exist
because the two sensitivities are different quantities with different signs:

| Shock type | Units of the shock | P&L | `beta_to_factor` carries |
|---|---|---|---|
| `RELATIVE_RETURN` | fractional return (`-0.35` = −35%) | $V \cdot \beta \cdot \Delta$ | return elasticity to the factor |
| `YIELD_BPS` | basis points (`200.0` = +200bp) | $-V \cdot D \cdot \Delta/10^4$ | modified or spread duration, in years |

A **fall** in a price-like factor is a loss for a long; a **rise** in a yield-like factor
is a loss for a long. The minus sign lives in the engine, not in your beta.

## When NOT to Use

- **As a capital requirement or a regulatory stress test.** No regulator-set methodology
  is implemented. MiFID II RTS 6 Article 10 is titled "Stress testing" but mandates a
  *systems capacity* test — message and trade volumes at twice the previous six months'
  peak — not a portfolio P&L scenario. BCBS d450 is guidance addressed to banks. See
  `references/standards.md` for what binds whom.
- **On an options book, without knowing what you are losing.** Every position is one
  linear beta or duration. There is no gamma, no vega convexity, no bond convexity and no
  cross-factor term, so a large shock on a convex book is wrong in the direction that
  matters — a short option loses far more than the linear estimate. Aggregate the Greeks
  with `options-greeks-real-time-portfolio-aggregation` and shock those instead.
- **To model an instrument that can trade through zero.** A `RELATIVE_RETURN` shock is
  bounded at −1.0. The May 2020 WTI contract settled at **−$37.63 on 20 April 2020**
  (CFTC interim staff report); no percentage shock reaches a negative price. Shock the
  position value directly.
- **To ask what it costs to get out.** This is a revaluation at shocked prices. The
  liquidation cost and the days-to-liquidate horizon are
  `portfolio-stress-test-including-liquidity-crunch-scenarios`.
- **To replay per-symbol crash returns.** If your scenario is "SPY −34%, QQQ −28%" rather
  than "equity factor −34%", use `stress-testing-against-historical-crash-scenarios`.
- **To find the scenario that breaks you.** `StressScenarioCategory.REVERSE_STRESS` is a
  label you may attach to a scenario you constructed; the engine does not solve for the
  shock that breaches a limit.
- **As a path-dependent drawdown model.** One instantaneous revaluation, not a path. The
  limit is expressed as a drawdown percentage but what is measured is a single-period
  scenario loss.

## Prerequisites

- Positions as `AssetPosition`: `asset_id`, `factor_name`, **signed** `current_value_usd`
  (a short is negative), `beta_to_factor`. One instrument may occupy several rows, one
  per factor it is exposed to — a convertible carries an equity row and a credit row.
- A capital base. `capital_base_usd` is the denominator for every percentage in the
  report and is **required whenever the book contains a short**; on a long-only book it
  defaults to the sum of position values.
- `max_allowed_drawdown_pct` (default $20.0$) — a **library default, not a regulatory
  limit**. Calibrate it to the capital the book must not lose and record why.
- Scenarios: the three predefined `HISTORICAL_CRISIS` replays, plus any
  `StressScenarioDefinition` you supply. Scenario ids must be unique across both.

## Workflow

1. **Map every position to a factor and a sensitivity.**
   - **Decision point — the factor name is a join key, and a typo is silent.** A position
     whose `factor_name` matches no shock contributes exactly zero. Version 1.0.0
     reported such a book as a $0 loss, no breach, and an empty worst-case scenario — an
     all-clear on a book it never stressed. Coverage is now explicit: read
     `report.status`, `report.factors_never_shocked` and each result's
     `unshocked_asset_ids` before quoting any number. A run in which *no* scenario
     touches *any* position raises.
   - Partial coverage is normal and is not an error: the 2022 rate scenario legitimately
     leaves an equity book untouched. It is reported, not rejected. BCBS d450 Principle 4:
     "If certain material and relevant risks are excluded from the scenarios, their
     exclusion should be explained and documented."

2. **Choose the shock type per factor, not per scenario.**
   - **Decision point — a rate or spread shock is `YIELD_BPS`, and its sign is the
     engine's.** $\Delta P/P \approx -D_{\text{mod}}\,\Delta y$. Version 1.0.0's
     absolute-change branch computed $+V\beta\,\Delta y$, so a +200bp hike against a
     duration-7 book reported a **+14% gain**; and its 2008 credit shock was written
     `3.00` with the relative flag — "+300bps" in the comment, +300% in the arithmetic,
     a **+$3,000,000 gain** on a $1M book in a spread blow-out. If you are migrating from
     1.0.0 and worked around either bug with a negative beta, remove the workaround: pass
     duration as a positive number.
   - The legacy `is_absolute_change=True` still constructs and now selects `YIELD_BPS`.
     Its numbers change sign. Prefer `shock_type=ShockType.YIELD_BPS`.

3. **Set the denominator deliberately.**
   - **Decision point — net exposure is not a capital base.** A market-neutral book nets
     to near zero and any loss over it explodes; a $1M/−$999k pair reported a −35% loss
     against a $1,000 denominator in 1.0.0. The engine now refuses to infer a base from a
     book containing shorts and requires `capital_base_usd`.

4. **Read the breach flag, and know what it compares.**
   - The flag is `loss_pct < -max_allowed_drawdown_pct`, evaluated **before** display
     rounding and **strictly** — a loss of exactly the limit passes, and −20.004% against
     a 20.0% limit breaches. Version 1.0.0 compared the two-decimal display figure and
     cleared that loss.
   - `breached_scenario_ids` collects every breach; breaches and incomplete coverage are
     logged at WARNING.

5. **Report and archive.** `results` carries the per-scenario breakdown — P&L, percentage,
   the value actually shocked, and the value that was not — so any aggregate traces to
   its drivers and a risk committee can see the scenario's reach, not just its number.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a gain on a rate hike.** The duration sensitivity is signed:
  $\Delta P/P \approx -D\,\Delta y$. Dropping the minus turns the single most common
  fixed-income stress scenario into good news, and nothing in the output looks wrong —
  the number is the right magnitude with the wrong sign.
- **Writing basis points into a relative shock.** `3.00` means +300% to a
  `RELATIVE_RETURN` factor and +300bp to a `YIELD_BPS` one. The comment beside the
  constant is not what runs.
- **Treating a $0 scenario loss as a pass.** Zero P&L means either nothing moved or
  nothing matched. Check `unshocked_asset_ids` before believing it, especially after a
  reference-data change renames a factor.
- **Reading a NaN as a pass.** Every comparison against NaN is False, so an unguarded NaN
  value clears `loss_pct < -limit` and lands in a report whose breach flag reads `False`.
  Non-finite inputs now raise.
- **Deciding a risk limit on a rounded number.** Display rounding belongs in the report,
  not in the comparison.
- **Measuring a hedged book against its own net exposure.** Drawdown limits are set
  against capital. State the capital base.
- **Duplicating a factor inside one scenario.** A `-0.30` shock beside a `-0.05` typo used
  to apply the `-0.05` silently, because the later key won. Both now raise.
- **Assuming diversification survives the scenario.** Shocks are applied independently per
  factor from a vector you supplied; there is no correlation model and no forced-seller
  feedback. If you do not believe two legs offset in a crunch, shock them apart — see
  `tail-correlation-between-strategies-under-stress`.
- **Applying spot shocks to options without Greeks.** A linear beta prices neither gamma
  nor vega; the error grows with exactly the shock sizes a stress test is for.
- **Mistaking the defaults for the episodes.** −35% equity is materially milder than the
  −56.8% peak-to-trough decline of the 2007–2009 bear market, and +150% vol is milder than
  the rise that carried VIX to its 80.86 close on 20 November 2008. They are library
  defaults. An uncalibrated default nobody has questioned is a scenario nobody chose.

## Verification

Run `python -m unittest discover -s skills/scenario-based-stress-testing-custom-shocks/scripts`
and confirm a 100% pass rate. The suite pins the behaviour below; every item in the first
group fails against version 1.0.0.

- **Rate shock sign.** $1,000,000 at modified duration 7 under +200bp $\Rightarrow$
  $-7 \times 0.02 \times \$1\text{M} = -\$140{,}000$ (−14.0%), not $+\$140{,}000$. A
  −150bp cut on $2,000,000 at duration 5 $\Rightarrow +\$150{,}000$.
- **Credit shock units.** $2,000,000 at spread duration 4 under the 2008 scenario's
  +300bp $\Rightarrow -\$240{,}000$ (−12.0%), not $+\$24{,}000{,}000$.
- **Threshold.** A loss of exactly −20.0% against a 20.0% limit passes; −20.004% breaches
  despite rounding to −20.00 for display.
- **Coverage.** A book whose only factor no scenario shocks raises `ValueError`. A book
  that is 80% `EQUITY_SPOT` and 20% `GOLD` returns
  `STRESS_TEST_INCOMPLETE_FACTOR_COVERAGE`, `factors_never_shocked == ["GOLD"]`, and a
  −$280,000 Lehman loss with `GLD` listed as unshocked.
- **Equity worked example.** $500k at β1.1 plus $500k at β0.9 under −35%
  $\Rightarrow -\$350{,}000$ (−35.0%), limit breached; under a custom −15%
  $\Rightarrow -\$150{,}000$, not breached.
- **Sign convention.** A −$100,000 short-vol position under a +150% vol shock loses
  $150,000; the mirror long gains it.
- **Negative checks.** NaN/±Inf/numeric-string/bool/None values or betas, a blank
  `asset_id` or `factor_name`, empty positions, a non-positive or non-finite
  `capital_base_usd`, a zero-value long-only book, a non-positive
  `max_allowed_drawdown_pct`, a duplicate `scenario_id`, a duplicate factor within one
  scenario, an empty shock list, a relative shock below −1.0, a short book with no
  `capital_base_usd`, a bare `StressScenarioDefinition` passed where a sequence is
  expected, a P&L that overflows to infinity, and wrongly typed positions or scenarios
  must each raise `ValueError`.
- **Positions given as a generator** are materialised, not consumed by the first scenario
  and reported as $0 for the rest.

## Related Skills

- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `stress-testing-against-historical-crash-scenarios`
- `risk-limit-calibration-against-historical-drawdowns`
- `risk-model-backtesting-against-realized-outcomes`
- `tail-correlation-between-strategies-under-stress`
- `correlation-aware-exposure-limits`
- `options-greeks-real-time-portfolio-aggregation`
- `value-at-risk-var-live-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
