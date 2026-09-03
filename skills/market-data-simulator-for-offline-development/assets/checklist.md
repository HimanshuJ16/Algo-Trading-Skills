# Pre-Flight / Sign-off Checklist — market-data-simulator-for-offline-development

## Scope (do this first)
- [ ] The fixture is being used to exercise **code paths**, not to evaluate strategy performance.
- [ ] Everyone reading the output knows a GBM path has i.i.d. Gaussian increments, so profit on it is fitted noise.
- [ ] Microstructure, queue-position, impact and event-behaviour questions have been routed to recorded data instead (`market-data-replay-harness-for-integration-testing`).

## Instrument parameters (neither is inferable from the price)
- [ ] `seconds_per_year` matches the instrument's trading clock — `TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH` for cash equities, `SECONDS_PER_YEAR_CONTINUOUS` for crypto and other 24/7 venues.
- [ ] `price_tick_size` matches the venue's minimum increment (0.01 for most NMS stocks ≥ \$1.00, 0.0001 below \$1.00, 0.25 for CME ES, 1e-8 for small-denomination crypto).
- [ ] For a sub-\$1 or small-denomination instrument, the tick size was lowered — the \$0.01 default would flatten the path or snap it to a single grid point.
- [ ] `spread_bps` is at least one tick wide at the simulated price level, or the `tick_constrained` flags are expected and understood.
- [ ] `price_tick_size` is an exact decimal (not `1/3` or an arithmetic result such as `3 * 1e-8`) and is far finer than the price — a config warning about quantisation error was investigated, not ignored.

## Determinism
- [ ] `random_seed` is set for anything stored, committed or compared — an unseeded run is flagged `deterministic=False` and must not become a fixture.
- [ ] `start_timestamp_epoch` is explicit or left at the fixed default; it is never wall-clock derived.
- [ ] Two runs of the same config were compared **including timestamps**, not just prices.
- [ ] The engine does not touch the global RNG: seeding `random` before and after a run leaves the caller's sequence unchanged.
- [ ] Price and depth draw from independent streams, so re-tuning depth leaves stored price fixtures byte-identical.

## Price path
- [ ] The GBM state is carried at full precision; quantisation is applied to the emitted value only.
- [ ] The Itô correction $-\tfrac{1}{2}\sigma^2$ is present — without it $\mathbb{E}[S_t] \neq S_0 e^{\mu t}$.
- [ ] Prices are strictly positive at every tick, including on sub-tick instruments.
- [ ] `realized_annualized_volatility` is within sampling error ($\approx 1/\sqrt{2n}$) of the configured $\sigma$.
- [ ] `realized_wall_clock_annualized_volatility` was checked — this is the figure that exposes a wrong clock, and it should equal $\sigma$ only on the continuous clock.
- [ ] An overflowing $\sigma\sqrt{\Delta t}$ raises naming the step rather than emitting `inf`.

## Quote construction
- [ ] $P_{\text{bid}} < P_{\text{ask}}$ at **every** tick, including at `spread_bps = 0` and at sub-tick spreads.
- [ ] $P_{\text{bid}} > 0$ at every tick; a non-positive bid raises rather than being published.
- [ ] The bid is floored and the ask ceiled — quotes are widened to the grid, never narrowed.
- [ ] `mean_quoted_spread_bps` was read rather than assuming the request was met (widen-only costs up to one tick).
- [ ] `tick_constrained_quote_count` was inspected on any instrument where the requested spread is near one tick.
- [ ] Every emitted bid, mid and ask is an exact multiple of `price_tick_size`, including on non-power-of-ten grids such as \$0.25.

## Timestamps
- [ ] `timestamp_nanos` is used wherever precision matters; `timestamp_epoch` is understood as a lossy float view (~238 ns resolution at a 2020 epoch).
- [ ] Timestamps are computed from the tick index, not accumulated, so they carry no drift.
- [ ] Tick spacing below 1 ns is rejected rather than emitting duplicate timestamps.

## Consumption
- [ ] Backtests fill at `bid_price` / `ask_price`, **never** at `last_price` — `last_price` is the mid, not a trade print, and filling at it pays zero spread.
- [ ] Report statistics are read as covering emitted ticks only; `initial_price` is never emitted.
- [ ] Long runs use `iter_synthetic_ticks()` or `retain_ticks=False` rather than materialising every tick.
- [ ] Nothing downstream treats the uniform depth values as a liquidity signal.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/market-data-simulator-for-offline-development/scripts` — 100% pass rate.
- [ ] The zero-volatility closed form $S_n = S_0 e^{\mu n \Delta t}$ is asserted against an independently derived value, not against the implementation's own formula.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
