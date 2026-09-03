---
name: intraday-vs-eod-backtest-granularity-tradeoffs
description: >-
  Backtest data-resolution advisor: audits the in-bar execution-path ambiguity that OHLC bars of any length leave unresolved for stop-loss strategies, matches resolution to the declared holding period, and sizes the dataset from a venue-specific session calendar.
domain: Quant Research & Alt Data
subdomain: Backtesting Engine Design & Data Granularity
tags: ["backtesting", "data-granularity", "ohlc-bias", "intraday-vs-eod", "tick-data", "compute-footprint", "simulation-bias"]
brokers_frameworks: ["Vectorized / Event-Driven Backtesters", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when choosing the bar resolution for a backtest, when buying a market data subscription to feed one, or when auditing why a simulated stop-loss strategy looks better on paper than it trades. An OHLC bar records four prices and **no path**. If a bar's High reaches the take-profit and its Low reaches the stop-loss, the bar cannot say which came first, so the simulator assumes an answer — and that assumption, not the strategy, decides whether the trade books a win or a loss.

The advisor takes a declared strategy profile and returns three things: the minimum resolution that can simulate it honestly, an explicit audit of the ordering ambiguity the *selected* resolution leaves unresolved, and a record-count/storage estimate for both the selected and the recommended dataset.

## When NOT to Use

- **As evidence that 1-minute bars removed the bias.** They did not. The ambiguity is a property of bars, not of *daily* bars — a stop placed a few ticks away sits inside a 1-minute bar's range as surely as inside a daily one. Finer bars shrink the unknown-ordering window; only genuine intra-bar data closes it. This engine reports `has_ohlc_sequence_bias = True` on **every** bar resolution when the strategy carries an intraday stop, including on an approved configuration.
- **As a storage quote.** `estimated_storage_size_gb` is a record count multiplied by an *assumed* bytes-per-record. That product is a planning figure — enough to tell gigabytes from terabytes, not enough to size a contract. Measure the real archive; see `historical-tick-data-storage-and-compaction`, which treats an assumed bytes-per-record as exactly the thing not to quote.
- **As a run-time prediction.** `data_volume_ratio_vs_recommended` counts records, not seconds. Actual slowdown depends on the engine, the I/O path, and whether the strategy vectorizes — see `vectorized-vs-event-driven-backtest-tradeoffs`.
- **As a fill model.** Tick data resolves *ordering*. It does not model queue position, spread capture, or impact — see `execution-realistic-simulation`.
- **With the default calendar on a non-US-equity venue.** The 390-minute session and 252-day year describe a US equity regular session. Futures, FX, and crypto run far longer; leaving the defaults in place understates those datasets several-fold.
- **As a substitute for reading the strategy.** Advice is derived entirely from the declared profile. A profile that misstates its holding period gets advice for the strategy it claims to be.

## Prerequisites

- Strategy profile: `holding_period`, `trade_frequency_per_day`, `has_intraday_stop_loss`, `universe_size`, `history_years`, `selected_data_granularity`.
- `trade_frequency_per_day` as round trips per day **per instrument**. 25 trades/day across a 500-name universe is one trade per name every 20 days — a positional strategy, not a scalper. Divide a portfolio-wide count by the universe size before passing it.
- The backtester's documented in-bar tie-break (`intrabar_fill_assumption`), read from its docs rather than assumed. It is `UNSPECIFIED` until someone has actually checked.
- The venue's session calendar (`session_minutes_per_day`, `trading_days_per_year`) when it is not a US equity regular session.
- Measured `bytes_per_record` and `compression_ratio` if the footprint estimate is going to be quoted to anyone.

## Workflow

1. **Declare and validate the profile.** Every enumerated field is validated against its allowed set and an unrecognized value raises. A misspelled resolution must not fall through to a default: silently sizing a tick dataset as a daily one is the smaller half of that bug — the larger half is that the stop-loss ambiguity audit keys off the resolution string, so a typo returned `GRANULARITY_APPROVED` on a config that had never been audited.
2. **Derive the minimum resolution from the holding period first.** Trade frequency escalates only *within* an intraday holding period (>= 50 round trips/day/instrument $\implies$ `TICK_L2`, otherwise `INTRADAY_1MIN`). A swing or positional strategy is floored at `DAILY_EOD`, or at `INTRADAY_5MIN` if it carries an intraday stop — because knowing whether that stop was touched requires intraday data even when the position is held for months. Letting a trade count outrank the holding period is what puts a monthly rebalance on minute bars.
3. **Audit the in-bar path, and grade it by severity rather than pass/fail:**
   - Intraday stop on `DAILY_EOD` $\implies$ `OHLC_SEQUENCE_BIAS_WARNING`. The whole trading day is one unordered High/Low pair.
   - Selected resolution coarser than the recommended one $\implies$ `INSUFFICIENT_RESOLUTION_WARNING`. Entry and exit falling inside a single bar cannot be simulated from that bar.
   - Intraday stop on any bar resolution with an `OPTIMISTIC` or `UNSPECIFIED` tie-break $\implies$ `IN_BAR_PATH_AMBIGUITY_WARNING`. The convention *is* the result.
   - Intraday stop on bars with a declared `PESSIMISTIC` tie-break $\implies$ approved, but the report still carries `has_ohlc_sequence_bias = True` and the notes say why: a stop-first convention bounds the error conservatively, it does not remove it.
4. **Check the other direction too.** A resolution two or more steps finer than needed $\implies$ `COMPUTE_OVERHEAD_WARNING`, reported as the measured record ratio between the two datasets rather than an invented speed multiplier.
5. **Size both datasets** from the venue's own session calendar: `trading_days_per_year x history_years x records_per_day x universe`, with `records_per_day` derived from `session_minutes_per_day` (a partial trailing bar counts) rather than hard-coded. Report the selected and recommended footprints side by side so the cost of following the advice is visible.
6. **Emit `BacktestGranularityReport`** with both footprints, their ratio, the ambiguity flag, the declared tie-break, and any profile-consistency warnings.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Backtesting an intraday stop on daily bars.** The daily High and Low are unordered, so every trade whose bar spans both levels is decided by the engine's tie-break. Platforms differ in which way they lean, and the optimistic direction inflates the equity curve without any code being wrong.
- **Believing minute bars fixed it.** They narrowed the window. A 2-tick stop is still inside a 1-minute bar's range on any volatile print. The fix is either a declared pessimistic convention or a detail-timeframe / tick replay of the active bar — not a finer bar alone.
- **Never checking which convention your backtester uses.** `backtesting.py` gives the stop-loss priority and its maintainer describes the stance as deliberately adversarial; Freqtrade evaluates stoploss before ROI within a candle and warns this yields *more* stop exits than live. Conventions are not uniform, which is why platforms ship explicit intra-bar modes at all — read yours rather than assuming it leans pessimistic. Reporting a Sharpe ratio without knowing which convention produced it is reporting the platform's default, not the strategy.
- **Reading a portfolio-wide trade count as a per-instrument rate.** It escalates a monthly-hold strategy onto minute bars and multiplies the dataset several hundred–fold for nothing.
- **Loading tick data for a positional strategy.** At the shipped planning assumptions, a 500-name 5-year tick archive holds ~63 billion records against 630,000 for the daily equivalent — five orders of magnitude more data for a strategy whose signal is unchanged by it.
- **Applying the US equity session to a futures or crypto backtest.** 390 minutes/day and 252 days/year understate a continuously traded venue by more than 5x on record count, and the shortfall lands in the storage plan, not in an error message.
- **Quoting the storage estimate as measured.** Bytes-per-record here is assumed. Multiply by a different plausible assumption and the same archive is half or twice the size.

## Verification

- Instantiate `BacktestGranularityAdvisorEngine()`. Size a 500-symbol, 5-year, 1-minute dataset on the US equity default calendar $\implies$ exactly $252 \times 5 \times 390 \times 500 = 245{,}700{,}000$ records and $9.153$ GiB at the assumed 40 bytes/record. The tick equivalent is $63{,}000{,}000{,}000$ records and $1{,}877.546$ GiB; the daily equivalent is $630{,}000$ records and $0.028$ GiB.
- Test the flawed config (`has_intraday_stop_loss=True`, `selected_data_granularity='DAILY_EOD'`) $\implies$ `OHLC_SEQUENCE_BIAS_WARNING` with `has_ohlc_sequence_bias=True`.
- Test the same strategy on `INTRADAY_1MIN` with no declared tie-break $\implies$ `IN_BAR_PATH_AMBIGUITY_WARNING`, **not** an approval; with `intrabar_fill_assumption='PESSIMISTIC'` $\implies$ approved, but `has_ohlc_sequence_bias` stays `True`.
- Test the silent-failure regression: a misspelled `selected_data_granularity` (e.g. `'DAILY'`) must raise `ValueError`, never return `GRANULARITY_APPROVED`.
- Test the venue calendar: a 1,440-minute/365-day crypto year yields $2{,}628{,}000$ one-minute bars per symbol over 5 years against $491{,}400$ on the US equity default.
- Run `python -m unittest discover -s skills/intraday-vs-eod-backtest-granularity-tradeoffs/scripts`.

## Related Skills

- `execution-realistic-simulation`
- `multi-timeframe-backtest-consistency-checks`
- `vectorized-vs-event-driven-backtest-tradeoffs`
- `historical-tick-data-storage-and-compaction`
- `backtest-infrastructure-cost-budgeting`
- `lookahead-bias-elimination`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `research-environment-vs-production-environment-parity`
