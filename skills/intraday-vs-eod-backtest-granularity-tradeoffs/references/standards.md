# Standards & Sources for Backtest Granularity Selection

## What is actually documented by venues and backtest engines

These are published behaviours, not this skill's choices. They constrain what any
bar-based simulator can and cannot conclude about an intraday stop-loss.

| Subject | Documented behaviour | Source |
|---|---|---|
| NYSE session length | The Core Trading Session runs "9:30 a.m. to 4:00 p.m. ET" — **390 minutes** — with the Core Open Auction at 9:30 and the Closing Auction at 16:00. Extended hours are outside this window. NYSE also scheduled three 1:00 p.m. early closes in 2026 (Jul 3, Nov 27, Dec 24), which produce 210-minute sessions. | [NYSE — Holidays & Trading Hours](https://www.nyse.com/markets/hours-calendars) |
| US equity trading days per year | Not a constant. Applying the published NYSE 2026 holiday list (10 holidays) to the 2026 weekday calendar gives **251** trading days. "252" is a planning approximation. | Same as above |
| In-bar path is unknown | Backtesting "lacks some detailed information about what happens within a candle" — with four data points per candle you cannot know whether the High preceded the Low. Freqtrade's documented mitigation is `--timeframe-detail`, which replays a finer timeframe inside active candles. | [Freqtrade — Backtesting: assumptions made by backtesting](https://www.freqtrade.io/en/stable/backtesting/) |
| Tie-break convention (pessimistic) | Freqtrade evaluates exits in the order exit-signal → **stoploss** → ROI → trailing stoploss within one candle, and warns that "Stoploss is evaluated before ROI within one candle", which can produce more stoploss exits in backtest than in dry/live. | Same as above |
| Tie-break convention (pessimistic) | In `backtesting.py`, when a plain market entry has both levels inside one bar, "SL has priority (the framework takes an adversarial, rather than an optimistic stance)." | [kernc/backtesting.py — Discussion #242](https://github.com/kernc/backtesting.py/discussions/242) |
| Tie-break convention (intra-bar option) | TradeStation exposes "Use Look-Inside-Bar back-testing" and "Enable Intra-bar Order Generation Optimization with Look-Inside-Bar back-testing", which evaluates the strategy as price changes within the bar rather than from the bar's OHLC alone. | [TradeStation — Strategy Group Settings](https://help.tradestation.com/10_00/eng/tsportfolio/strategy_groups/strategy_group_settings.htm) |

**Reading of the above:** the ambiguity is a property of *bars*, not of *daily* bars,
and mainstream engines resolve it by convention rather than by inference. Two widely
used engines default to the pessimistic (stop-first) convention; the optimistic
direction is what inflates a backtest. Which convention is in force is therefore a
required input to interpreting any stop-loss backtest, at any bar length.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is a venue,
vendor, or regulatory standard.**

| Rule | Requirement | Why |
|---|---|---|
| Field validation | An unrecognized `selected_data_granularity`, `holding_period`, or `intrabar_fill_assumption` MUST raise, never default. | The ambiguity audit keys off the resolution string; a value that falls through to a default returns `GRANULARITY_APPROVED` on a config that was never audited. |
| Ambiguity scope | `has_ohlc_sequence_bias` MUST be True for an intraday-stop strategy on **any** bar resolution, not only `DAILY_EOD`. | Bars of every length omit the path. Clearing the flag at 1-minute would assert something the data cannot support. |
| Tie-break disclosure | An `UNSPECIFIED` or `OPTIMISTIC` in-bar fill assumption on a stop-loss strategy MUST be reported as a warning. | The convention determines the outcome of every trade whose bar spans both levels. |
| Pessimistic result semantics | A `PESSIMISTIC` tie-break MUST be reported as a conservative **bound**, not as an unbiased result. | Stop-first is a deliberate lower bound, not a measurement of what happened. |
| Holding-period precedence | The recommendation MUST be gated on holding period; trade frequency MAY escalate only within an intraday holding period. | A portfolio-wide trade count read as a per-instrument rate escalates a positional strategy onto minute bars. |
| Intraday stop on a long hold | A swing or positional strategy carrying an intraday stop MUST be floored at intraday data. | Whether the stop was touched is an intraday question regardless of how long the position is intended to be held. |
| Under-resolution | A selected resolution coarser than the recommended one MUST be flagged. | Entry and exit inside one bar cannot be simulated from that bar's OHLC. |
| Over-resolution | A selection two or more steps finer than recommended MUST be flagged with the **record ratio**, not a claimed speed multiplier. | Run time depends on the engine and I/O path; only the record count is known here. |
| Session derivation | Bars per day MUST be derived from `session_minutes_per_day`, not hard-coded. | 390 describes a US equity regular session only. |
| Footprint honesty | The storage figure MUST be documented as records × an **assumed** bytes-per-record, in GiB (2^30 B), and MUST NOT be presented as a measurement. | See `historical-tick-data-storage-and-compaction`: an assumed bytes-per-record times a row count is not a measurement of anything. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `DEFAULT_SESSION_MINUTES_PER_DAY` | 390 | Sourced: NYSE Core Trading Session, regular hours only. Wrong for futures, FX, crypto, and for any run that includes extended hours or half-days. |
| `DEFAULT_TRADING_DAYS_PER_YEAR` | 252 | Conventional approximation; the true NYSE 2026 count is 251. |
| `DEFAULT_TICKS_PER_SYMBOL_PER_DAY` | 100,000 | **Heuristic. No venue or vendor publishes this.** Real per-symbol message rates span orders of magnitude by instrument, day, and whether L2 depth updates are included. Supply a measured value. |
| `DEFAULT_BYTES_PER_RECORD` | 32 / 40 / 40 / 48 B | Assumed uncompressed widths. Override with your own schema's measured widths. |
| `HIGH_FREQUENCY_TRADES_PER_DAY` | 50.0 | Engineering choice for when tick replay becomes the default. Not a published threshold. |
| `compression_ratio` | 1.0 (uncompressed) | Pass only a ratio measured on your own encoder. |

## Scope boundary

This skill decides **data resolution and dataset size**. It does not model fills,
spreads, queue position, or market impact (`execution-realistic-simulation`), does not
choose an engine architecture (`vectorized-vs-event-driven-backtest-tradeoffs`), and
does not price the compute (`backtest-infrastructure-cost-budgeting`).
