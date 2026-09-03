---
name: stress-testing-against-historical-crash-scenarios
description: >-
  Use when replaying the positions you hold right now against per-symbol historical
  crash returns such as 2008, 2020 or a flash crash, to quantify tail P&L. Factor-mapped
  shocks with betas are scenario-based-stress-testing-custom-shocks.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-management, stress-testing, crash-scenarios, tail-risk, drawdown-analysis, scenario-coverage
  brokers_frameworks: "Custom Risk Engine; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when the question is "what does the book I am holding *right now* lose
if the market repeats a specific past crash?" VaR is calibrated to a historical window and
collapses when the regime breaks; a deterministic replay is not, which is the point of
running one alongside it. Typical uses:

- Pre-trade risk approval gates that block new entries when the stressed loss exceeds a
  stated fraction of NAV.
- Periodic portfolio risk reports to a risk committee, where the number has to trace back
  to named positions and a dated scenario window.
- Sanity-checking that drawdown circuit breakers would fire before a tail event does real
  damage — see `kill-switch-and-drawdown-circuit-breakers`.

The engine is a **per-symbol return replay**: each scenario maps `symbol -> cumulative
return` and each position is revalued as `quantity * price * shock`. Quantities are
**signed** — a short is negative — and `portfolio_nav` is the capital base for every
reported percentage.

## When NOT to Use

- **As a regulatory stress test or a capital requirement.** No regulator-set methodology
  is implemented. SEC Rule 15c3-5 mandates pre-trade order controls, not portfolio
  scenario analysis; MiFID II RTS 6 Article 10 is titled "Stress testing" but requires a
  *systems capacity* test; BCBS d450 is guidance addressed to banks. See
  `references/standards.md` for what binds whom.
- **On an options book, without converting to Greeks first.** A shock is a proportional
  move in the underlying and P&L here is linear in position value. An option's P&L is not,
  and the error grows with exactly the shock sizes a stress test exists for. Aggregate
  with `options-greeks-real-time-portfolio-aggregation` and shock those.
- **To ask what it costs to get out.** This is a revaluation at shocked prices. Liquidation
  cost, days-to-liquidate and the margin spiral are
  `portfolio-stress-test-including-liquidity-crunch-scenarios`.
- **To shock a factor rather than a symbol.** If your scenario is "equity factor −34%,
  rates +200bp" rather than "SPY −34%, QQQ −28%", use
  `scenario-based-stress-testing-custom-shocks`, which carries betas and durations.
- **On an instrument that can trade through zero.** A return shock is bounded at −1.0 and
  the engine rejects anything below it. The May 2020 WTI contract settled at −$37.63; no
  percentage shock reaches a negative price. Shock the position value directly.
- **As a path-dependent drawdown model.** One instantaneous revaluation, not a path. There
  is no margin call, no forced liquidation and no rebalancing in between.

## Prerequisites

- A crash scenario library. The three shipped in `BUILTIN_SCENARIOS` are **broad-market
  proxy defaults with dated windows, not reconstructions of the episodes**; each carries
  `window_start`, `window_end`, `basis` and a `calibration_note` saying where it sits
  against the record. Recalibrate before relying on them.
- Current positions as `symbol -> signed quantity` and `symbol -> current price`. Every
  held, non-flat symbol needs a price; ones without are reported, not silently dropped.
- `portfolio_nav > 0` — the capital base, not net exposure.
- `max_stressed_loss_pct` (default `0.15`) — a **library default, not a regulatory
  limit**. Calibrate it to the capital the book must not lose and record why.

## Workflow

1. **Build the scenario library, and date every scenario.**
   - **Decision point — a shock magnitude is meaningless without its window and basis.**
     An asset's return across the *equity index's* peak-to-trough window is not that
     asset's own worst move inside the episode. Gold ended the 19 Feb – 23 Mar 2020 window
     down about 3.8% but fell roughly 12% between 9 and 19 March in the dash for cash. A
     book long gold as a crash hedge stressed on the window return is flattered roughly
     threefold on that leg. Record which you used in `basis` and be consistent.
   - **Decision point — do not mix single-day and peak-to-trough scenarios without saying
     so.** The shipped 2015 flash crash is a single-day intraday move; the other two are
     multi-month peak-to-trough. Ranking them against each other on magnitude compares
     different quantities.
   - Supply single-name shocks yourself, from point-in-time data. The library ships none:
     the names a library would hard-code are the ones that survived, which is the
     survivorship bias this skill warns about. Version 1.0.0 shipped TSLA −80% and
     META −50% in its 2008 scenario for securities that had not yet listed.

2. **Replay the current positions, and read the coverage fields before the P&L.**
   - **Decision point — a $0 loss means either nothing moved or nothing matched.** Check
     `report.status`, `unpriced_symbols`, `unshocked_symbols` and `fallback_symbols`
     before quoting any number. `STRESS_TEST_INCOMPLETE_COVERAGE` means the reported loss
     understates the book. A run in which nothing could be priced raises rather than
     returning a vacuous all-clear.
   - `fallback_symbols` names every position priced off the scenario's `DEFAULT` rather
     than a symbol-specific historical return. A book stressed entirely off `DEFAULT` has
     not replayed anything; it has applied one assumption uniformly.

3. **Identify the worst case on the signed P&L.**
   - The worst scenario is the minimum **signed** `stressed_pnl_pct`, not the largest
     magnitude. `worst_loss_pct` and `worst_loss_usd` are loss magnitudes floored at zero;
     `worst_pnl_pct` and `worst_pnl_usd` carry the same outcome signed so a gain stays
     visible.

4. **Enforce the gate, and know what it compares.**
   - `threshold_breached` is `worst_loss_pct >= max_stressed_loss_pct`, so a stressed loss
     of **exactly** the threshold is a breach. It is evaluated on the unrounded figure.
   - A scenario gain can never fire the gate. Version 1.0.0 took `abs()` of the worst
     percentage, so a net-short book that profited in every scenario was reported as
     losing that amount and blocked from trading. If you worked around that by inverting
     the sign on short quantities, remove the workaround and pass the signed quantity.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a scenario gain as a loss.** A crash replay is a *profit* for a net-short
  book. Taking the absolute value of the worst percentage turns that profit into a breach
  and blocks trading on a book that made money — and nothing in the output looks wrong,
  because the magnitude is right and only the sign is missing.
- **Treating a $0 stressed loss as a pass.** Positions with no price, and positions no
  scenario names, contribute exactly zero. Read the coverage fields first, especially
  after a reference-data change renames a symbol.
- **Reading a NaN as a pass.** Every comparison against NaN is False, so an unguarded NaN
  price clears `worst_loss_pct >= limit` and lands in a report whose breach flag reads
  `False`. Non-finite inputs now raise.
- **Letting a fallback masquerade as history.** A `DEFAULT` shock is an assumption applied
  to symbols the scenario never studied. Version 1.0.0 went further and applied a
  hard-coded −30% when a scenario had no `DEFAULT` at all — an unsourced magnitude inside
  a report labelled as a historical replay.
- **Survivorship bias in the scenario itself.** A shock vector built from the names that
  still trade today omits the ones that went to −100%. Build it from point-in-time
  constituents — see `survivorship-bias-free-universe-construction`.
- **Shocking a security that did not exist in the window.** Any single-name shock for a
  pre-IPO period is fabricated, not conservative. Check the listing date against
  `window_start`.
- **Confusing a window return with an adverse move.** Hedge legs bottom on different dates
  than the equity leg. One calendar window applied to every asset understates whichever
  leg you were relying on to protect you.
- **Assuming diversification survives the scenario.** Shocks are applied independently
  from a vector you supplied; there is no correlation model. Cross-asset correlations
  spike toward 1.0 in a crash — see `correlation-aware-exposure-limits` and
  `tail-correlation-between-strategies-under-stress`.
- **Mistaking one revaluation for the crash.** Real crashes unfold over days with margin
  calls and forced liquidation amplifying the loss. This engine has none of that; the
  headline return is a floor, not a ceiling.
- **Measuring against net exposure instead of capital.** `portfolio_nav` is the capital
  base. A long/short book nets to near zero and any loss over that denominator explodes.

## Verification

Run `python -m unittest discover -s skills/stress-testing-against-historical-crash-scenarios/scripts`
and confirm a 100% pass rate. The suite pins the behaviour below; every item in the first
group fails against version 1.0.0.

- **Scenario gain is not a loss.** A 1,000-share SPY short at $100 against a $100,000 NAV
  under −52% and −34% replays gains $52,000 and $34,000. `worst_pnl_usd` is `+34,000`,
  `worst_loss_pct` is `0.0`, and the gate does not fire. Version 1.0.0 reported a 34.00%
  loss and blocked new entries.
- **NaN raises.** A NaN price raises `ValueError` instead of producing
  `threshold_breached=False` with `breach_reason=None`.
- **Coverage.** An unpriced held position is reported in `unpriced_symbols` with status
  `STRESS_TEST_INCOMPLETE_COVERAGE`; a symbol no scenario names appears in
  `unshocked_symbols` and contributes $0 rather than a hard-coded −30%; a symbol priced
  off `DEFAULT` appears in `fallback_symbols`. An empty book, and a book in which nothing
  could be priced, both raise.
- **No pre-listing shocks.** No built-in scenario shocks a symbol whose listing date
  postdates its `window_start`; `2008_GFC` contains neither TSLA nor META.
- **Worked example.** 100 AAPL at $150 under −20% plus 200 MSFT at $300 under −10% is
  $-3,000 + $-6,000 = $-9,000 on a $75,000 NAV, −12.00%, no breach at a 50% limit.
- **Hedge offset.** $80,000 SPY under −30% plus $20,000 TLT under +15% nets
  $-24,000 + $3,000 = $-21,000, −21.00% of a $100,000 NAV, breaching a 15% limit.
- **Threshold.** A −15.00% loss against a 15% limit breaches; −14.99% does not.
- **Numeric types.** `Decimal` and `numpy` scalars are accepted as quantities and prices;
  bools, strings and bytes are not.
- **Negative checks.** Non-finite or non-numeric quantities, prices, NAV, threshold or
  shocks; a bool passed as a number; a non-positive NAV or threshold; an empty book; a
  blank or `DEFAULT`-named position key; non-dict positions or prices; an empty scenario
  library; a duplicate scenario name; a scenario with no shocks; a shock below −1.0; a
  wrongly typed scenario; and a stressed P&L that overflows to infinity must each raise
  `ValueError`.

## Related Skills

- `scenario-based-stress-testing-custom-shocks`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `value-at-risk-var-live-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
- `correlation-aware-exposure-limits`
- `tail-correlation-between-strategies-under-stress`
- `risk-limit-calibration-against-historical-drawdowns`
- `survivorship-bias-free-universe-construction`
- `options-greeks-real-time-portfolio-aggregation`
