---
name: vectorized-vs-event-driven-backtest-tradeoffs
description: >-
  Use when choosing between a vectorized and an event-driven backtest engine, or when
  measuring how much of a vectorized result is an artefact of its fill assumption rather
  than the strategy.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, vectorized-backtest, event-driven-backtest, execution-drag, fill-latency, transaction-costs, engine-parity
  brokers_frameworks: "NumPy; Python Standard Library; backtesting.py; Backtrader; VectorBT; Zipline"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

# Vectorized vs Event-Driven Backtest Tradeoffs

Two questions, one skill. **Which engine can honestly simulate this strategy?** And
**how much of the reported performance is the strategy rather than the fill
assumption?**

The helper answers the second by running the same signal series through a matched
pair of engines that differ in exactly one variable at a time, so the gap between
them decomposes into a transaction-cost component and a fill-latency component.
Unmatched engines cannot measure anything: if the two disagree for structural
reasons — different position units, different compounding, different signal timing
— the "execution drag" they report is the mismatch, not the execution.

## When to Use

- When deciding whether a parameter sweep can run vectorized, or whether the
  strategy's execution makes that answer meaningless.
- When a vectorized backtest looks profitable and you need to know how much of the
  edge survives realistic costs and a realistic fill delay — separately, because
  the two have different remedies.
- When a vectorized and an event-driven backtest of the same strategy disagree and
  you need to know whether that gap is execution or a modelling artefact.
- When quoting a runtime figure for a backtest engine choice, and the honest answer
  is a measurement on your own workload rather than a number from a blog post.

## When NOT to Use

- **As a production backtester.** There is no order book, no partial fill, no queue
  position, no bid/ask, no market impact, no borrow cost, no corporate action and no
  margin model. It consumes a close series and a target-exposure series. For a fill
  model with spread, size-dependent impact and a real fee stack, see
  `execution-realistic-simulation`; for passive fills, `queue-position-modeling-for-passive-orders`.
- **As a look-ahead audit.** The engines honour the timing convention they are
  given; they cannot tell whether the signal you passed in was computable at the bar
  it is indexed to. That screen is `lookahead-bias-elimination`.
- **To justify a speedup claim you have not measured.** `speedup_factor` is `None`
  below 5,000 bars, deliberately. See *Common Pitfalls*.
- **To decide bar resolution.** Whether daily bars can represent the strategy at all
  is `intraday-vs-eod-backtest-granularity-tradeoffs`; this skill assumes the
  resolution is already settled.
- **As a cost model.** `commission_bps` and `slippage_bps` are flat rates you supply.
  They do not vary with size, liquidity or venue, and a flat rate calibrated on a
  liquid large-cap will understate an illiquid one. Calibrate with
  `transaction-cost-analysis-tca-integration`.

## Prerequisites

- A close series (strictly positive, chronologically ordered, no gaps you have not
  accounted for) and a **target-exposure** series of the same length.
- `signals[t]` is the target exposure **as a fraction of equity** decided at the
  close of bar t — `1.0` fully invested long, `-1.0` fully short, `0.0` flat. It is
  not a share count and not a lot count. Values above `max_abs_exposure` (default
  1.0) raise rather than silently backtesting leverage.
- The bar size, expressed as `periods_per_year`. The default 252 is daily US equity
  bars. Minute bars are not daily bars, and annualizing them at 252 is wrong by a
  factor of ~20.
- Your assumed execution delay in bars, for `execution_lag_bars`. If you have not
  decided, the default of 1 matches `backtesting.py`'s documented next-bar default.
- Python 3.10+ and NumPy. No other dependency.

## Workflow

1. **Check whether a vectorized backtest can represent the strategy at all**, before
   any question of accuracy. `recommend_engine` treats two features as *blocking*
   rather than as weighted inputs:
   - **Path-dependent stops.** Whether the position is still open at bar t depends
     on the realized path since entry, so the exposure series is not knowable before
     the run. There is nothing to multiply the return vector by. Engines that appear
     to vectorize this (VectorBT and similar) JIT-compile the loop rather than
     removing it — a faster event loop, not array algebra.
   - **Limit or other passive orders.** A limit order may not fill. A vectorized
     engine has no representation of an unfilled order, so it applies the exposure
     unconditionally — the assumption that every order filled, at the best price.

   Neither is a matter of degree, so neither can be outvoted by a low trade count.
2. **Treat turnover as the matter of degree it is.** The advisor computes estimated
   annual friction as `trades/day × bars/year × exposure change per trade × cost
   rate` and compares it against a stated tolerance (default 2% of equity a year).
   At 1 trade a day and 10 bps round trip that is 25.2% a year — the fill assumption
   is then driving the result, not refining it. The figure is returned in
   `estimated_annual_cost_drag_pct` so you can disagree with the tolerance rather
   than with a hidden trade-count cutoff.
3. **Run the vectorized engine for the search.** `run_vectorized_backtest` is NumPy
   array arithmetic: exposure over bar t+1 is `signals[t]`, filled instantly at bar
   t's close, costs charged as a haircut proportional to `|signals[t] - signals[t-1]|`.
   Equity compounds. Use it for parameter grids, where the ranking matters more than
   the level.
4. **Confirm the survivors event-driven.** `run_event_driven_backtest` submits the
   target decided at bar t and fills it at the close of bar `t + execution_lag_bars`,
   at a slipped price, through a signed cash ledger. Do not promote a parameter set
   on the vectorized number alone.
5. **Read the drag decomposition, not a single number.** `compare_engines` returns
   three curves — frictionless, vectorized, event-driven — and therefore two
   attributable gaps: `cost_drag_pct` (costs alone, fill assumption held constant)
   and `return_drag_pct` (latency and slippage alone, costs held constant). A large
   cost drag says trade less or trade cheaper; a large latency drag says the edge
   decays inside your execution delay, which sizing down will not fix.
6. **Check `total_turnover` before believing any drag figure.** Cost drag is turnover
   times the cost rate. A drag number reported without the turnover that produced it
   cannot be interpreted, and cannot be compared across strategies.
7. **Measure the speedup; do not quote one.** `speedup_factor` is `None` below 5,000
   bars because timing a sub-millisecond workload measures the clock. Above it, the
   number is one run on one machine.
8. **Set `rebalance_every_bar` deliberately if the strategy shorts or levers.** The
   vectorized `w · r` product assumes the target weight is restored every bar. A
   fully-invested long satisfies that for free; a short does not, and drifts as the
   price moves. The default `False` is the realistic behaviour and will diverge from
   the vectorized curve even at zero cost and zero latency.

> Full procedure: see `references/workflows.md`.
> Engine comparison table and evidence base: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing two engines that were never commensurable.** The most expensive error
  here, and the one the pre-2.0.0 implementation made: its event-driven engine held
  `signals[t]` as a **share count** against a fixed $100,000 while its vectorized
  engine used exposure fractions. On an identical long-only series the two reported
  49.00% and 0.06%. That 48.94-point "execution drag" was a unit mismatch. Before
  believing any cross-engine gap, set costs to zero and latency to zero and confirm
  the two agree; if they do not, the harness is measuring itself.
- **Summing arithmetic returns in one engine and compounding an equity curve in the
  other.** They differ on identical returns, and the difference grows with the
  length and volatility of the series. It will be reported as execution drag.
- **Charging a flat cost per position change instead of per unit of turnover.** A
  −1 → +1 reversal moves two units of exposure and costs twice a 0 → +1 entry.
  Charging both the same understates every reversal by half, which flatters exactly
  the high-turnover strategies whose costs matter most.
- **Quoting a speedup constant.** There is no universal figure. The ratio scales
  with how many events the loop must process per unit of vectorizable work, so a
  monthly rebalance and a per-bar signal are not comparable. Published third-party
  benchmarks on realistic strategies land in single- to low-double-digit multiples
  (~6–8× and ~20×; see `references/standards.md`), and this module's own engines
  measured 12–46× depending on series length. The pre-2.0.0 documentation asserted
  "1,000× faster" and set a "≥50× speedup" verification gate; no source supports
  either, and the implementation that shipped with them was a pure-Python loop that
  measured **0.78×** — slower than the event engine it claimed to beat.
- **Expecting a fixed percentage haircut from realistic fills.** The pre-2.0.0 text
  claimed a "10-30% performance haircut". No source supports a universal figure, and
  the arithmetic says there cannot be one: cost drag is turnover × cost rate, which
  is unbounded above. Turning over 337 units of exposure at 10 bps consumes 33.7% of
  equity in costs — in one sampled run that moved the reported total return by 39
  percentage points. Turning over 2 units a year consumes 0.2%. There is no single
  haircut between those two strategies, only their turnover.
- **Annualizing a Sharpe ratio at 252 regardless of bar size.** A minute-bar
  strategy annualized at 252 is wrong by about a factor of 20. Pass
  `periods_per_year`.
- **Reading a cross-engine Sharpe difference as if it were distribution-free.** The
  √T annualization assumes i.i.d. returns, and lagged execution induces exactly the
  serial correlation that breaks it — Lo (2002) reports annual Sharpe ratios
  overstated by as much as 65% on serially correlated returns. Read `return_drag_pct`,
  which needs no distributional assumption, alongside `sharpe_divergence`.
- **Treating a Sharpe ratio computed on a near-constant return series as real.** A
  constant series has a sample standard deviation of ~1e-17 of float noise, not
  exactly zero, so a naive `std or 0.0001` guard does not fire. The pre-2.0.0 code
  reported a Sharpe ratio of **1.6e15** for a constant +1%/bar series. This module
  returns NaN, because a series with no dispersion has no risk-adjusted return.
- **Routing a stop-driven strategy to the vectorized engine because its trade count
  is low.** Trade count and structural representability are different questions. A
  weighted score that lets one outvote the other will send a path-dependent strategy
  to the engine that cannot express it.

## Verification

Run the suite from the repository root:

```bash
python -m unittest discover -s skills/vectorized-vs-event-driven-backtest-tradeoffs/scripts -v
```

41 tests. Expected values are derived independently of the implementation — equity
paths worked through by hand on three- and four-bar series, the Sharpe ratio checked
against `statistics.mean`/`statistics.stdev` from the standard library rather than
the module's own NumPy call — so they cannot pass by restating the module's algebra.

The load-bearing check is `TestEngineParity`: with costs, latency and weight drift
all switched off, the two engines must produce **identical** equity curves
(`rtol=1e-12`). Everything this skill reports is a difference between those curves,
so any structural disagreement invalidates the measurement. `TestNoLookAhead`
asserts that equity through bar k is unchanged by arbitrary edits to the data after
bar k, in both engines.

Seven behaviours are pinned as explicit regressions and fail against pre-2.0.0:
engine parity, the cash-ledger sign on sells, compounding, turnover-proportional
costs, the NaN Sharpe guard, blocking engine recommendations, and input validation.

## Related Skills

- `lookahead-bias-elimination` — the timing screen this skill assumes has already passed.
- `execution-realistic-simulation` — a real fill model, once you know latency drag matters.
- `transaction-cost-analysis-tca-integration` — calibrates the flat cost rates this skill takes as given.
- `intraday-vs-eod-backtest-granularity-tradeoffs` — settles bar resolution before engine choice.
- `execution-slippage-attribution-timing-vs-sizing` — decomposes the drag this skill measures at the fill level.
- `backtest-vs-live-performance-divergence-tracking` — the same question once live fills exist.
- `backtest-parameter-sensitivity-analysis` — what the fast vectorized engine is for.
- `walk-forward-validation-setup` — the validation frame a parameter sweep belongs inside.
- `backtest-determinism-and-reproducibility` — makes the two engines' runs comparable across machines.
- `queue-position-modeling-for-passive-orders` — required for the limit-order strategies this skill refuses to vectorize.
