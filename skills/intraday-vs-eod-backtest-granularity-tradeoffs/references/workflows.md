# Workflows for Backtest Granularity Assessment

## 1. Declare and validate the strategy profile

- Capture `holding_period`, `trade_frequency_per_day`, `has_intraday_stop_loss`,
  `universe_size`, `history_years`, `selected_data_granularity`.
- Express `trade_frequency_per_day` as round trips per day **per instrument**. Divide
  a portfolio-wide count by `universe_size` first.
- Read `intrabar_fill_assumption` out of the backtester's own documentation. Leave it
  `UNSPECIFIED` until someone has actually checked — the field exists to record
  whether the question was asked, not to be filled in optimistically.
- Reject unrecognized enumerated values instead of defaulting them. A resolution
  string that falls through to a default skips the ambiguity audit and returns an
  approval for a configuration nobody audited.

## 2. Derive the minimum viable resolution

| Holding period | Intraday stop? | Minimum resolution |
|---|---|---|
| `INTRADAY_MINUTES` / `INTRADAY_HOURS`, >= 50 round trips/day/instrument | either | `TICK_L2` |
| `INTRADAY_MINUTES` / `INTRADAY_HOURS`, < 50 | either | `INTRADAY_1MIN` |
| `SWING_DAYS` / `POSITIONAL_MONTHS` | yes | `INTRADAY_5MIN` |
| `SWING_DAYS` / `POSITIONAL_MONTHS` | no | `DAILY_EOD` |

Holding period gates the decision; trade frequency escalates only within an intraday
holding period. A long-horizon strategy that still carries an intraday stop needs
intraday data — whether the stop was touched is an intraday question no matter how
long the position was meant to be held.

## 3. Audit the in-bar execution path

Grade by severity rather than pass/fail:

1. `OHLC_SEQUENCE_BIAS_WARNING` — intraday stop on `DAILY_EOD`. One unordered
   High/Low pair per trading day.
2. `INSUFFICIENT_RESOLUTION_WARNING` — selected resolution coarser than the
   recommended one. Entry and exit inside one bar cannot be simulated from that bar.
3. `IN_BAR_PATH_AMBIGUITY_WARNING` — intraday stop on any bar resolution with an
   `OPTIMISTIC` or `UNSPECIFIED` tie-break.
4. `COMPUTE_OVERHEAD_WARNING` — selection two or more steps finer than recommended.
5. `GRANULARITY_APPROVED` — otherwise. Note that an approved stop-loss strategy on
   bars still reports `has_ohlc_sequence_bias = True`: the declared pessimistic
   tie-break bounds the error, it does not remove it.

Remediation, in increasing order of cost:

- Declare and verify the engine's tie-break, and treat a stop-first result as a
  conservative bound.
- Replay a detail timeframe inside active bars (Freqtrade's `--timeframe-detail`,
  TradeStation's Look-Inside-Bar) so the ordering is observed rather than assumed.
- Move to tick/L2 replay, which resolves ordering outright — and still does not model
  queue position or spread capture.

## 4. Estimate the dataset footprint

```
records_per_day = ceil(session_minutes_per_day / bar_minutes)   # bars
                = ticks_per_symbol_per_day                      # TICK_L2
                = 1                                             # DAILY_EOD
records         = trading_days_per_year x history_years x records_per_day x universe
size_gib        = records x assumed_bytes_per_record / compression_ratio / 2**30
```

- Derive bars per day from the venue's session length. The 390-minute default is a US
  equity regular session; futures, FX, and crypto days are several times longer.
- Size the **recommended** dataset alongside the selected one, so the cost of taking
  the advice is visible before it is taken.
- Report the ratio between them as a record count. It is not a run-time multiplier.
- Treat the size as an order of magnitude. Bytes-per-record is assumed, not measured.

## 5. Emit the report

Output `BacktestGranularityReport` carrying both footprints, their record ratio, the
status, the ambiguity flag, the declared tie-break, and any profile-consistency
warnings (for example a per-instrument trade rate that contradicts the declared
holding period).
