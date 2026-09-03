---
name: tick-size-pilot-program-impact-assessment
description: >-
  Use when a change in the minimum pricing increment — the SEC Tick Size Pilot's $0.05 test groups, the amended Rule 612 $0.005 tier, a MiFID II RTS 11 band reclassification, or a venue retick — must be measured rather than assumed: Rule 605 share-weighted quoted, effective and realized spread decomposition, top-of-book depth and queue impact, order-to-trade ratio and fill rate, and the execution-algorithm parameters those measurements should move.
domain: Market Microstructure
subdomain: Order Book Dynamics
tags:
- market-microstructure
- tick-size-pilot
- spread-decomposition
- rule-605
- adverse-selection
- queue-dynamics
- algo-execution
brokers_frameworks:
- SEC Rule 605 (17 CFR 242.605; definitions at 17 CFR 242.600(b))
- SEC Rule 612 (17 CFR 242.612, minimum pricing increment)
- Tick Size Pilot Program NMS Plan (approved 2015-05-06; ran 2016-10-03 to 2018-09-28)
- MiFID II RTS 11 (Commission Delegated Regulation (EU) 2017/588)
- Python standard library (dataclasses, enum, math)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a minimum pricing increment has changed — or is about to — and the desk needs a measured answer to "what did that do to our execution costs and our queue?" rather than an inference from the tick ratio. Concretely:

- **A US retick.** The amended Rule 612 introduces a `$0.005` quoting increment for symbols the listing exchange designates tick-constrained (Time Weighted Average Quoted Spread `<= $0.015`). It is adopted but **not yet operative**: SEC exemptive relief of 2026-06-11 (Release 34-105656) deferred compliance to the first business day of **November 2027**. When it lands it is a *narrowing* for the affected symbols — the mirror image of the Pilot.
- **An EU band reclassification.** An RTS 11 liquidity band changes with the annual ADNT calculation, moving an instrument's tick without any venue rule changing.
- **A venue retick** on any exchange that revises its price-step schedule by circular.
- **Retrospective study** of the SEC Tick Size Pilot Program itself, whose 20 TB of collected data remains the largest controlled tick-size experiment on record.

The engine decomposes spreads into **quoted**, **effective** and **realized (5-minute)** components on the Rule 605 formulas, computes top-of-book depth, order-to-trade ratio, share fill rate and adverse selection in basis points, compares two regimes, and maps the measured deltas onto passive market-making, TWAP/VWAP slicing, momentum-taking and stat-arb parameters.

## When NOT to Use

- **To produce or check a Rule 605 report.** The formulas match, but two things do not: Rule 605 measures effective spread against the NBBO midpoint **at the time of order receipt** (17 CFR 242.600(b)(8)), whereas this engine measures against the midpoint of the snapshot carrying the trade. Rule 605 also requires realized spread at five horizons (50 ms, 1 s, 15 s, 1 min, 5 min), not one. Numbers from this engine are research and TCA inputs, not a filing.
- **To decide whether an order price is legal.** This engine measures the consequences of a tick regime; it does not validate a price against one. For price alignment and tick compliance, use `exchange-tick-size-regime-tracking`.
- **To forecast the effect of a proposed retick.** The published Pilot outcomes range from a `-17%` spread change to `+203%` depending purely on the security's pre-change spread class. There is no transferable multiplier, and this engine deliberately hard-codes no effect size.
- **On a sample that does not isolate the tick change.** A pre/post comparison with no control group attributes every concurrent market-wide move — a volatility regime, an index reconstitution — to the tick. The Pilot used a randomly stratified control group and difference-in-differences for exactly this reason.
- **On quote-only data, to reason about execution costs.** Without trades the engine returns `None` for effective spread, realized spread and adverse selection. That is the correct answer, not a gap to fill.

## Prerequisites

- Python 3.9+ (standard library only).
- L1 quote snapshots with bid/ask price and size, timestamped to at least millisecond precision.
- Trade records tagged with **aggressor side**, from exchange execution records where available, or a signing rule (Lee-Ready, tick test) where not — signing error propagates directly into effective and realized spread.
- **Trade size on every trade**, or the engine falls back to equal weighting and says so. Rule 605 spread averages are share-weighted (17 CFR 242.600(b)(8), (12), (13)); an equal-weighted average over-counts odd lots.
- The consolidated midpoint 5 minutes after each execution, already honouring the end-of-session proviso in 17 CFR 242.600(b)(13): where fewer than 5 minutes of regular trading hours remain, the midpoint of the **final** NBBO of the session is the correct input. Where it cannot be observed, pass `None`.
- Matched baseline and test samples for the same symbol, ideally with a control group of untouched symbols measured over the identical window.

## Workflow

1. **Assemble `TickSnapshot` series for the baseline and test regimes.** One snapshot per quote; attach `last_trade_price`, `last_trade_size`, `last_trade_is_buy` and `future_mid_price_5m` to the snapshot the trade printed against.
   - **Decision point — can you observe the 5-minute midpoint?** If the horizon runs past the close, apply the Rule 605 proviso and use the session's final NBBO midpoint. If you cannot, pass `None`; the trade is then excluded from the realized-spread sample rather than imputed.

2. **Evaluate each regime** — `evaluate_microstructure_metrics(symbol, regime, snapshots, total_messages=, total_fills=, total_shares_ordered=, total_shares_executed=)`.
   - **Decision point — how dirty is the feed?** The default `InvalidSnapshotPolicy.SKIP` excludes crossed quotes, non-finite values and non-positive prices, counts them in `excluded_snapshot_count`, and continues. Use `RAISE` only on a feed you expect to be clean. Always read `excluded_snapshot_count` before trusting the result: a large exclusion count is a data problem, not a market finding.
   - **Decision point — check `weighting` before comparing.** `SHARE_WEIGHTED` means every trade carried a size. `EQUAL_WEIGHTED` means at least one did not, and the spread averages are no longer comparable to a Rule 605 figure or to a share-weighted baseline.
   - Order-to-trade ratio is `total_messages / total_fills`. Share fill rate is `total_shares_executed / total_shares_ordered` — the Pilot's definition. These are different measurements from different denominators; supplying only the message counters leaves the fill rate `None`.

3. **Compare** — `compare_regimes(baseline, test)`.
   - Percentage-change fields are `None` when the baseline metric is absent, zero or negative, and are named in `undefined_metrics`. **A zero baseline effective spread is a real outcome** — every print at the midpoint — not a data error, so it is reported as undefined rather than as an infinite or sign-flipped percentage.
   - `fill_rate_change_pp` is a difference in **percentage points**. The others are percentage changes. Do not mix them in a report.
   - **Decision point — did quoted and effective diverge?** They usually do, and the gap is the finding. Under the Pilot, quoted spreads widened 14–24% while share-weighted effective spreads rose 54–59% in cents per share; the two are reported against different denominators and neither is a proxy for the other.

4. **Recalibrate** — `recommend_strategy_tuning(algo_type, comparison)` for `PASSIVE_MARKET_MAKING`, `TWAP_VWAP_SLICING`, `MOMENTUM_TAKER` or `STAT_ARB`.
   - Output is **advisory screening only**. Every branch is gated on the module-level screening constants (`SPREAD_FINDING_THRESHOLD_PCT`, `DEPTH_FINDING_THRESHOLD_PCT`, `ADVERSE_SELECTION_FINDING_BPS`, `MARKET_MAKING_ADVERSE_SELECTION_BPS`, `SLICING_SPREAD_THRESHOLD_PCT`), which are this engine's reporting triggers — not regulatory limits and not empirical constants. Tune them to the desk's tolerances.
   - An undefined input metric produces an explicit "Cannot assess…" line rather than silence, so an empty recommendation list means "measured, nothing triggered" and never "could not measure".

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading the tick ratio as the cost ratio.** A `$0.01` → `$0.05` retick is a 5x wider tick, and the Pilot's quoted spreads rose 14–24% while share-weighted effective spreads rose ~54–59%. Neither is 400%, because midpoint executions and price improvement absorb much of a widened quote. Setting a TWAP price cap from the tick ratio prices in a cost the book never charged.
- **Equal-weighting the spread averages.** Rule 605 share-weights every spread statistic. An equal-weighted mean gives a 100-share odd lot the same influence as a 10,000-share block and will not reconcile against any published execution-quality figure.
- **Substituting one metric for another when data is missing.** Quoted spread is not a stand-in for effective spread, and half the effective spread is not a stand-in for realized spread. A fabricated component silently becomes a fabricated adverse-selection number and then a fabricated recalibration.
- **Ignoring the end-of-session proviso.** A trade at 15:58 has no midpoint 5 minutes later. Rule 605 requires the session's final NBBO midpoint; carrying the next session's open instead prices an overnight gap into the realized spread and reports it as adverse selection.
- **Letting one crossed quote kill the batch.** Crossed and locked NBBOs are transient but real in consolidated data. A locked quote's spread genuinely is zero; a crossed quote is not a measurement and must be excluded and counted, not averaged in and not fatal.
- **Comparing a test period to a bare pre-period.** Without a control group the estimate absorbs every market-wide change in the window. The Pilot's own headline numbers are difference-in-differences against a randomly stratified control group, and several of them were not statistically significant even so.
- **Assuming a widened tick means a worse fill rate.** It did not in the Pilot: shares executed rose from 1.1% to 2.2% in Test Group 3, against 1.2% → 1.5% for the control. Deeper queues and higher fill rates coexisted because the trade-at prohibition pushed volume onto displayed quotes.
- **Confusing fill rate with the reciprocal of the order-to-trade ratio.** Fills per message and executed shares per ordered share answer different questions; reporting one under the other's name understates or overstates passive performance by whatever the average order size is.
- **Treating the amended Rule 612 half-penny tier as live.** It is deferred to November 2027 and is a **per-symbol assignment** by the listing exchange, not a function of price. A baseline built on the assumption that tick-constrained symbols already quote in half-pennies is measuring a regime that does not yet exist.
- **Citing RTS 28 for EU tick sizes.** The MiFID II tick regime is **RTS 11** (Commission Delegated Regulation (EU) 2017/588). RTS 28 was the top-five-execution-venue report, and that obligation was removed in the MiFID II/MiFIR review (ESMA deprioritised supervision from 2024-02-13).

## Verification

- Reproduce the Pilot Assessment's published worked example (footnote 11): selling at `10.00` into a `10.00 x 10.05` quote gives an effective spread of `0.05`; selling at `10.01` into the same quote gives `0.03`; a print at `10.025` gives exactly `0.0`.
- Confirm share weighting: 100 shares at an effective spread of `0.05` and 900 at `0.02` must average `0.023`, not `0.035`.
- Confirm a quote-only sample returns `None` for `avg_effective_spread`, `avg_realized_spread_5m` and `adverse_selection_bps` — never the quoted spread and never half of it.
- Confirm `calculate_quoted_spread(10.00, 10.00)` returns `0.0` (locked) and `calculate_quoted_spread(10.05, 10.00)` raises (crossed).
- Confirm a crossed snapshot inside a batch is skipped and counted in `excluded_snapshot_count` under the default policy, and raises under `InvalidSnapshotPolicy.RAISE`.
- Confirm `compare_regimes` returns `None` — not `ZeroDivisionError`, not a sign-flipped percentage — for a zero or negative baseline effective spread, and names the metric in `undefined_metrics`.
- Confirm a `+400%` quoted spread with a nearly flat effective spread does **not** trigger a TWAP passive re-weighting.
- Run the suite and confirm a 100% pass rate:

```bash
python -m unittest discover -s skills/tick-size-pilot-program-impact-assessment/scripts
```

## Related Skills

- `exchange-tick-size-regime-tracking`
- `order-book-depth-processing-l2-l3`
- `adverse-selection-measurement-for-passive-orders`
- `queue-position-modeling-for-passive-orders`
- `peg-order-types-for-passive-execution`
- `execution-slippage-attribution-timing-vs-sizing`
