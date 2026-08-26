---
name: multi-timeframe-backtest-consistency-checks
description: Use when verifying that lower-resolution bars a strategy trades on (e.g.
  5-min, 15-min) agree with the same bars independently resampled from higher-resolution
  data (e.g. 1-min), so boundary-anchor, gap, and aggregation defects are caught before
  a backtest is trusted.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- multi-timeframe
- resampling
- signal-consistency
- data-resolution
- boundary-alignment
brokers_frameworks:
- Timeframe Consistency Checker
- Python
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a strategy trades on bars it did not receive natively — resampled from 1-minute bars or ticks, or supplied by a vendor whose aggregation you have not verified. The check is a **cross-provenance comparison**: build the low-resolution series yourself from high-resolution input, obtain the same series independently, and confirm the two agree field-for-field.

The failures it catches are silent. A 5-minute bar anchored to the wrong boundary still looks like a valid bar; a bucket missing one 1-minute source bar still has a plausible high and low. Nothing raises, nothing is `NaN`, and the backtest reports a Sharpe ratio computed on prices that never traded at those times.

## When NOT to Use

- **As a substitute for having two data sources.** The check is a *comparison*. With only one series there is nothing to compare it against, and any single-source "consistency" figure is the tool re-deriving its own arithmetic. If you have only one provenance, this skill cannot validate it.
- **To compare an indicator across different resolutions.** SMA(3) on 5-minute bars and SMA(15) on 1-minute bars are different estimators over different observation sets. They disagree on every non-flat series no matter how correct the resampling is, so their divergence measures nothing. This is the defect that version 1.0.0 shipped; see `references/standards.md`.
- **As a data-cleaning step.** `Bar` rejects non-finite values and OHLC relation violations rather than repairing them. Bad-tick filtering belongs upstream — see `backtest-outlier-and-bad-tick-filtering`.
- **For DST transitions, half-days, or holiday calendars.** Timestamps are treated as epoch seconds with no session calendar. See `daylight-saving-time-transition-handling` and `global-exchange-holiday-calendar-handling`.
- **On tick data directly.** The input must already be uniform bars of a declared interval; the checker validates that every gap is a whole multiple of that interval.

## Prerequisites

- High-resolution bars, strictly time-ordered, of one known interval (`bar_interval_seconds`).
- An **independently sourced** low-resolution series covering the same period.
- The venue's session start time, needed to choose the boundary anchor (see Workflow step 1).

## Workflow

1. **Choose the boundary anchor before anything else.** `ANCHOR_EPOCH` buckets on the fixed grid running from the Unix epoch (equivalent to pandas' default `origin="start_day"` when timestamps are UTC); `ANCHOR_SESSION` buckets from the first bar of the series (pandas `origin="start"`).
   - **Decision point — the two anchors agree only when the session open sits on the epoch grid.** NSE's capital market segment opens at 09:15 IST = 03:45 UTC = 13500 s after UTC midnight. $13500/900 = 15$, so 15-minute buckets align under either anchor; $13500/1800 = 7.5$, so a 30-minute epoch-anchored bucket opens at 09:00 IST and the day's first bucket holds only half a session. Check the arithmetic for your venue and interval rather than accepting the default.

2. **Resample the high-resolution series by wall-clock time.** `resample_bars()` buckets on timestamps, never on position in the list.
   - **Decision point — inspect `incomplete_buckets` before trusting the output.** A non-zero count means source bars are missing inside a bucket, so its high, low, and volume are computed over partial data and will legitimately differ from a reference built from a complete series. Fix the gap; do not widen the tolerance to accommodate it.
   - **Decision point — a trailing partial bucket is dropped by default.** A period still forming is not a finished bar, and presenting one as final leaks information from an incomplete period. Set `drop_incomplete_final=False` only for live intra-bar inspection, never for a backtest.

3. **Run `check_resampling_integrity()` first.** This compares every aggregated OHLCV field against the reference exactly. Aggregation is arithmetic, not estimation, so a correct series matches to floating-point noise and no percentage tolerance is required.
   - **Decision point — read `field_mismatches` to classify the defect.** A `volume`-only mismatch is a double-counting or scaling bug. Mismatches across `open`/`low`/`close` together indicate a boundary-anchor disagreement — retry with the other anchor before hunting for an aggregation bug. A large `missing_in_reference` or `missing_in_resampled` count means the two series do not cover the same buckets at all.

4. **Then run `check_consistency()` for indicator parity.** The same indicator, at the same period, on the same timeframe, across both provenances. `sma_period` is expressed in low-resolution bars and applied unchanged to both sides — it is deliberately not scaled by the resample factor.
   - **Decision point — the check raises `InsufficientDataError` rather than returning a verdict it did not earn.** Zero overlapping points is not a pass. If it raises, supply more history or confirm both series use the same anchor; do not catch and ignore it.

5. **Interpret divergence with the price level in mind.** `max_divergence_pct` is relative to the reference, so the same absolute error reads ten times larger on a $10 instrument than on a $100 one. Read `max_absolute_divergence` alongside it, and use `worst_timestamp` to locate the offending bucket.

> Full procedure: see `references/workflows.md`.
> Threshold rationale and the version 1.0.0 defect record: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chunking by list position instead of wall-clock time.** `bars[i:i+5]` looks like resampling and is not. One missing 1-minute bar shifts every subsequent bucket by one bar for the rest of the series, and the resulting bars remain perfectly well-formed — the defect surfaces only as a strategy that backtests well and trades badly.
- **Letting the input series define the grid.** A series that begins at 09:02 does not make 09:02 a 5-minute boundary. Anchoring to the first bar received silently straddles every real boundary when the download happened to start mid-bucket.
- **Setting the tolerance from what the data currently shows.** A one-bar boundary error on a price near 100 is about 0.1%. Version 1.0.0 published a 1.0% threshold, so it reported PASSED on exactly the defect it existed to catch. Resampling is exact; the tolerance belongs at floating-point noise, and loosening it needs a recorded reason.
- **Comparing an indicator across resolutions and calling the difference an error.** SMA(3) on 5-minute bars and SMA(15) on 1-minute bars differ by a constant on any trending series. Because the difference is reported as a percentage, the same correct data passes at one price level and fails at another — the verdict tracks the price, not the bug.
- **Treating a still-forming bucket as a completed bar.** The last bucket of a live feed usually holds fewer bars than the factor because the period has not ended. Emitting it as final gives the backtest a close price the market had not yet set.
- **Reading "0 comparisons, consistent" as a pass.** Insufficient history, a factor that produces no overlap, or two series on different anchors all yield nothing to compare. A checker that answers "consistent" to that is asserting something it never tested.
- **Volume double-counting during aggregation.** Summing an already-aggregated field, or summing overlapping windows, inflates volume while leaving OHLC correct — which is why the integrity check compares volume as its own field rather than folding it into a single verdict.

## Verification

- Build 50 one-minute bars with closes $100 + 0.1i$ and the matching 10 five-minute bars derived by hand. `check_resampling_integrity(..., factor=5, bar_interval_seconds=60)` must report `compared_buckets == 10`, `mismatched_buckets == 0`; `check_consistency(..., sma_period=3)` must report `max_divergence_pct == 0.0` over exactly 8 matched signals.
- Regression — boundary error: build a reference whose closes come from bar $5j+3$ instead of $5j+4$. The default checker must fail it, with `max_absolute_divergence == 0.1` and `max_divergence_pct < 1.0` — confirming the divergence is real and that the old 1.0% threshold would have passed it.
- Regression — price-level independence: the same series shape at base 100 and base 10 must both report `max_divergence_pct == 0.0`.
- Regression — gap handling: drop the 09:03 bar from ten 1-minute bars. Buckets must remain labelled `[0, 300]` with `incomplete_buckets == 1`, not shift.
- Anchor arithmetic: 60 one-minute bars from 09:15 IST at `factor=30` must open the first epoch-anchored bucket at 03:30 UTC with `incomplete_buckets == 1`, and the session-anchored run must produce two whole buckets with `incomplete_buckets == 0`.
- Negative checks: empty input, insufficient history, non-monotonic or duplicate timestamps, a gap that is not a multiple of the interval, a non-positive `factor`/`sma_period`/`bar_interval_seconds`, an unknown anchor, a non-finite price, and an OHLC relation violation must each raise.
- Run `python scripts/test_timeframe_consistency.py` and confirm 100% pass rate.

## Related Skills

- `lookahead-bias-elimination`
- `backtest-outlier-and-bad-tick-filtering`
- `intraday-vs-eod-backtest-granularity-tradeoffs`
- `backtest-determinism-and-reproducibility`
- `daylight-saving-time-transition-handling`
- `multi-exchange-feed-normalization`
