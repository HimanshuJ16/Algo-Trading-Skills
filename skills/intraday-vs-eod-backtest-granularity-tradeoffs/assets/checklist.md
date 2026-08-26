# Pre-Flight Checklist — Backtest Data Granularity

## Strategy profile
- [ ] Is the holding period declared (`INTRADAY_MINUTES` / `INTRADAY_HOURS` / `SWING_DAYS` / `POSITIONAL_MONTHS`)?
- [ ] Is `trade_frequency_per_day` a **per-instrument** round-trip rate, not a portfolio-wide count?
- [ ] Is the resolution recommendation gated on the holding period, with frequency escalating only within an intraday one?
- [ ] Does a swing or positional strategy carrying an intraday stop get intraday data rather than daily bars?

## In-bar execution path
- [ ] Does the strategy rely on an intraday stop-loss or take-profit?
- [ ] Is it understood that the ambiguity exists on bars of **every** length, not only daily bars?
- [ ] Has the backtester's in-bar tie-break been read out of its documentation rather than assumed?
- [ ] Is that tie-break pessimistic (stop first), and is the result therefore consumed as a conservative **bound** rather than an unbiased estimate?
- [ ] If the tie-break is optimistic or unknown, has a detail-timeframe or tick replay been used before the result is quoted?
- [ ] Is `has_ohlc_sequence_bias = True` on an approved run understood as accurate rather than as a bug?

## Resolution matching
- [ ] Is the selected resolution at least as fine as the recommended minimum?
- [ ] Can entry and exit both fall inside a single selected bar? (If so, the resolution is too coarse.)
- [ ] Is a resolution far finer than needed justified by something other than habit?

## Dataset sizing
- [ ] Is `session_minutes_per_day` the **venue's** session, not the 390-minute US equity default?
- [ ] Is `trading_days_per_year` right for that venue (252 is a US equity approximation; NYSE 2026 is 251; a continuous venue is 365)?
- [ ] Is `ticks_per_symbol_per_day` a measured rate from your own feed rather than the 100,000 planning heuristic?
- [ ] Is `bytes_per_record` from your actual schema?
- [ ] Is `compression_ratio` measured on your own encoder, not guessed?
- [ ] Is the storage figure reported as GiB (2^30 B) and labelled as an estimate, never as a measurement?
- [ ] Has the **recommended** dataset been sized too, so the cost of following the advice is known?

## Interpretation
- [ ] Is `data_volume_ratio_vs_recommended` read as a record count, never as a run-time multiplier?
- [ ] Is it understood that tick data resolves ordering but not queue position, spread, or impact?
- [ ] Do unrecognized enumerated values raise rather than silently default?
