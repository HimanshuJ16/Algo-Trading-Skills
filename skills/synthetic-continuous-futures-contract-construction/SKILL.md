---
name: synthetic-continuous-futures-contract-construction
description: >-
  Splices individual futures contract histories into a continuous back-adjusted series for backtesting, using volume/open-interest/calendar roll triggers and additive or proportional adjustment, with the newest contract left at real market prices and roll decisions taken only from completed sessions.
domain: Data Management & Quantitative Infrastructure
subdomain: Futures Market Data Engineering
tags: ["futures", "continuous-series", "back-adjustment", "ratio-adjustment", "roll-trigger", "contract-month-codes", "market-data", "look-ahead-bias"]
brokers_frameworks: ["CME Group Contract Month Codes", "CSI Unfair Advantage Back-Adjustment Convention", "Pandas", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a futures strategy needs one long price history and the raw data is a sequence of separately expiring contracts. Concatenating those contracts unadjusted puts a gap at every roll — the size of the calendar spread, not of any market move — which fabricates signals for anything trend-, breakout- or volatility-based and corrupts backtested P&L.

The engine makes both defining decisions explicit and auditable:

- **When to roll** — `VOLUME_CROSSOVER`, `OPEN_INTEREST_CROSSOVER`, or `DAYS_BEFORE_EXPIRY` (calendar days, requires expiration dates).
- **How to splice** — `ADDITIVE_BACK_ADJUSTMENT` (default), `PROPORTIONAL_RATIO`, or `UNADJUSTED_CONCATENATED` for a deliberately un-spliced series.

Both adjusted methods anchor on the **newest** contract, which is what "back-adjusted" means: CSI Data's manual states that the roll delta "is added to the past contract prices" and "the new contract prices remain unaffected by the back-adjustment splicing process." The final bar of the output is therefore a price you could actually have traded.

## When NOT to Use

- **To answer questions about historical price levels.** A back-adjusted 1990 crude print is not what crude cost in 1990; it is that price plus every roll gap since. No construction method is simultaneously correct on absolute levels and on returns — pick per question, and never read a level off an adjusted series.
- **To compute percentage returns from an additive series.** Adding a constant to older prices changes every percentage change computed on them. Use `PROPORTIONAL_RATIO` for return work, or compute returns per contract segment and chain them.
- **To decide when to roll a live position.** That is an execution problem with First Notice Day and delivery risk attached — see `futures-contract-roll-automation`. This skill answers the *series* question, and the two can legitimately use different roll dates.
- **As a point-in-time series.** Every roll retroactively restates all earlier bars. A back-adjusted history stored today does not equal the one stored last quarter, so it is not reproducible unless the roll schedule and adjustment method are stored with it.
- **For calendar-spread or curve research.** Term-structure work needs the individual contracts and their relationship. A continuous series has already discarded exactly that information.
- **For equity corporate actions.** Splits and dividends are a different adjustment problem — see `adjusted-vs-unadjusted-price-series-pitfalls`.

## Prerequisites

- One `pandas.DataFrame` per contract, keyed by contract symbol, indexed by session, carrying at least `close` plus whichever of `volume` / `open_interest` the roll method needs. `open`, `high`, `low`, `volume` and `open_interest` are carried through when present, and `open`/`high`/`low` receive the same adjustment as `close`.
- **One index label type across every contract**, and one bar per contract per session. Mixed `str` / `Timestamp` indexes and duplicate labels are rejected rather than silently mis-sorted.
- **Two-digit year codes** (`ESZ24`, not `ESZ4`) if you rely on symbol parsing for contract ordering, or an explicit `contract_expiries` map. Symbols are decoded with the CME month codes F, G, H, J, K, M, N, Q, U, V, X, Z = January…December.
- `contract_expiries` (`{'ESH24': '2024-03-15', ...}`) is **mandatory** for `DAYS_BEFORE_EXPIRY`, and is authoritative for ordering whenever supplied — pre-2000 history needs it, since two-digit year codes resolve into 2000-2099.
- Python 3.9+, `pandas`.

## Workflow

1. **Order the contracts by expiration, never alphabetically.**
   - **Decision point.** `sorted()` on symbols puts `ESH25` before `ESZ24` and silently inverts the whole series: the deferred contract becomes the front, every gap flips sign, and the output still looks like a plausible price history. The engine decodes the month code and year, or uses `contract_expiries` when given, and raises on anything it cannot order — including a single-digit year code (`ESZ4` is 2014 or 2024) and a mix of two products in one call.

2. **Evaluate the roll trigger only on sessions that have already closed.**
   - **Decision point — the crossover session is not the switch session.** A volume or open-interest crossover on session *t* is computed from *t*'s completed totals, which nobody knows until *t* closes. Pricing *t*'s bar off the back contract would import information from the future into that bar. The engine evaluates the trigger on *t* and makes the switch effective on *t+1*.
   - **Decision point — a missing trigger value is not "no crossover".** A NaN volume compares false against everything, so the naive form silently never rolls. The engine counts the session in `unevaluable_trigger_sessions` and logs it.
   - `min_confirmation_sessions` (default 1) requires the crossover to hold for N consecutive sessions. The default matches most vendors and rolls on the first crossover — including one caused by a single anomalous print, after which the series never comes back to the front contract.

3. **Measure the gap on the roll-from session, from both closes at once.**
   - `gap = close(back, t) - close(front, t)` and `ratio = close(back, t) / close(front, t)`, both observed on session *t*, the last session priced off the front contract. Taking the two prices from different sessions folds a day of market movement into what is supposed to be a pure calendar spread.
   - The gap is positive in contango and negative in backwardation. It is expressed in the product's own quote units — index points for ES, dollars per barrel for CL — not necessarily a currency.

4. **Apply the adjustment backwards from the newest segment.**
   - Segment *j* is shifted by the gaps (additive) or the product of the ratios (proportional) of every roll that happens *after* it. The newest segment gets offset 0.0 / factor 1.0 and keeps real market prices.
   - **Decision point — proportional adjustment requires strictly positive closes at every roll.** WTI settled at -$37.63 on 2020-04-20; a ratio through that print is meaningless. The engine raises and names `ADDITIVE_BACK_ADJUSTMENT` as the alternative rather than emitting a sign-flipped series.

5. **Read the output as three separate things.** `raw_*` columns are the traded contract's own prices, `adjusted_*` are the spliced series, and `adjustment_offset` / `adjustment_factor` record exactly what was applied to each bar. `segment_id` and `is_roll_session` identify which contract each bar came from and which bars are the first of a new segment — the bars where an execution assumption is least likely to hold.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive concatenation.** Appending raw contract prices leaves a gap the size of the calendar spread at every roll. On a 20-year ES series that is roughly 80 fabricated jumps, each one a trend signal that never happened.
- **Adjusting the wrong end of the series.** Shifting the *newer* prices and leaving the oldest ones real is forward adjustment. It still looks continuous — every roll gap is removed — so nothing in the output complains, but the last bar no longer matches the live quote, and every stop distance, contract count and level-based rule computed from it is off by the cumulative gap.
- **Reading percentage returns off an additive series.** A +25 point shift applied to a 1,000-point price and the same shift applied to a 5,000-point price are different percentages. Additive back-adjustment preserves *point* differences and destroys *percentage* ones; ratio adjustment does the reverse.
- **Labelling a series with a method that was never applied.** A series tagged `PROPORTIONAL_RATIO` whose adjusted column equals its raw column is not ratio-adjusted — it is unspliced data wearing a label, and it carries every discontinuity the skill exists to remove.
- **Rolling on the same session the crossover is observed.** The full-session volume that triggers the roll is not knowable until the session closes. Using it to select that session's own bar is look-ahead, and it shows up as a suspiciously well-timed edge around roll dates.
- **Premature rolling.** Rolling before liquidity has actually migrated prices the series off a thin contract, whose closes are wide-spread prints rather than a real market.
- **Negative back-adjusted history.** Persistent backwardation means repeated negative gaps, cumulating downward into the past until early prices cross zero. This is arithmetic, not a data error — CSI notes back- and forward-adjusted contracts "can include negative numbers", while ratio-adjusted series "are unlikely to go negative." A log transform or any percentage calculation on the negative region produces NaN or nonsense.
- **Assuming a stored back-adjusted file is reproducible.** Every new roll rewrites the entire history. Two backtests run three months apart on "the same" adjusted series are not running on the same numbers unless the roll schedule was pinned.
- **Comparing vendors' continuous series and concluding one is wrong.** There is no standard roll date. A crossover series and a five-days-before-expiry series legitimately disagree on both the roll date and every adjusted price before it.
- **Dropping sessions silently.** When the active contract has no bar for a session that exists elsewhere in the data, that session cannot appear in the output. The engine counts it in `sessions_without_active_bar` — a non-zero count on a liquid product means a data hole, not a holiday.

## Verification

- **Splice arithmetic**: three ES contracts each gaining 1.00 per session, each deferred trading exactly 10.00 above the one in front, rolling twice. The additive series must be exactly `[120, 121, 122, 123, 124, 125]` — every session-over-session delta 1.00, `cumulative_gap` 20.00, and the final bar equal to its own `raw_close`.
- **Anchor**: under both `ADDITIVE_BACK_ADJUSTMENT` and `PROPORTIONAL_RATIO`, the last bar's `adjusted_close` must equal its `raw_close`, with `adjustment_offset == 0.0` and `adjustment_factor == 1.0`.
- **Roll timing**: a crossover on 2024-03-02 must leave 2024-03-02 priced off the front contract and make 2024-03-03 the first back-contract bar, with `reference_session` 2024-03-02 and `effective_session` 2024-03-03.
- **Ordering**: `{'ESH25', 'ESZ24'}` must roll `ESZ24 -> ESH25` with a positive gap. Alphabetical ordering produces the reverse and a negative gap.
- **Proportional**: the adjusted history must differ from the raw history, `cumulative_ratio != 1.0`, and no session-over-session return may exceed the largest single-contract move.
- **Rejections**: empty input, a missing trigger column, duplicate index labels, mixed index types, a NaN close on a used bar, `DAYS_BEFORE_EXPIRY` without `contract_expiries`, a single-digit year code, two products in one call, and a non-positive close under proportional adjustment must each raise `ValueError`.
- **Negative prices**: a -37.63 close must be rejected under `PROPORTIONAL_RATIO` and handled under `ADDITIVE_BACK_ADJUSTMENT` (gap 57.63).
- Run `python -m unittest discover -s skills/synthetic-continuous-futures-contract-construction/scripts` and confirm 54/54 pass.

## Related Skills

- `futures-contract-roll-automation`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `lookahead-bias-elimination`
- `vendor-specific-adjustment-methodology-reconciliation`
- `futures-expiry-week-liquidity-and-volatility-handling`
- `backtest-determinism-and-reproducibility`
