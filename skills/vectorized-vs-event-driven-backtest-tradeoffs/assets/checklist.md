# Pre-Flight / Sign-off Checklist — vectorized-vs-event-driven-backtest-tradeoffs

Use this before quoting any cross-engine drag figure or promoting a parameter set.

## Input contract

- [ ] **Exposure units:** `signals[t]` is a target exposure fraction of equity, not
      a share or lot count. Values outside ±`max_abs_exposure` raise.
- [ ] **Decision timestamp:** `signals[t]` is computable from data up to and
      including bar t. Screened separately with `lookahead-bias-elimination` — this
      module cannot check it.
- [ ] **Bar size declared:** `periods_per_year` matches the actual bar size. The
      252 default is daily US equity bars, not minute bars.
- [ ] **Price series:** strictly positive, finite, chronologically ordered, with
      gaps and session boundaries accounted for.

## Harness validity — do this before believing any drag number

- [ ] **Engine parity:** with `commission_bps=0`, `slippage_bps=0`,
      `execution_lag_bars=0` and `rebalance_every_bar=True`, the two engines produce
      identical equity curves (`rtol=1e-12`). If they do not, the harness is
      measuring itself and every drag figure is an artefact.
- [ ] **No look-ahead:** equity through bar k is unchanged by arbitrary edits to the
      data after bar k, in both engines.
- [ ] **Scale invariance:** reported percentage returns do not change when
      `initial_capital` changes.

## Engine selection

- [ ] **Blocking features screened first:** path-dependent stops and limit/passive
      orders force the event-driven engine regardless of trade count. A vectorized
      run on either is undefined, not merely optimistic.
- [ ] **Turnover arithmetic reviewed:** `estimated_annual_cost_drag_pct` was read,
      and `max_tolerable_annual_cost_drag` was accepted or deliberately overridden.
- [ ] **Weight drift decided:** `rebalance_every_bar` was set knowingly if the
      strategy shorts or levers, and the divergence from the vectorized curve at
      zero cost is understood and expected.

## Results

- [ ] **Drag decomposed:** `cost_drag_pct` and `return_drag_pct` were read
      separately. A single combined number cannot distinguish a cost problem from a
      latency problem, and they have different remedies.
- [ ] **Turnover reported alongside drag:** cost drag is turnover × cost rate; a
      drag figure without its turnover cannot be interpreted or compared.
- [ ] **Sharpe caveats respected:** `periods_per_year` matches the bar size; a NaN
      Sharpe ratio is read as "no dispersion", not as a failure; `sharpe_divergence`
      is read alongside `return_drag_pct` because √T annualization assumes i.i.d.
      returns and lagged execution breaks that assumption.
- [ ] **No ruin warning in the log**, or the run is discarded — metrics computed
      past a zero-crossing in equity are arithmetic, not results.
- [ ] **Speedup measured, not quoted:** `speedup_factor` is `None` below 5,000 bars
      by design. No fixed multiple (and no fixed percentage haircut) is repeated
      from documentation without measuring it on this workload.

## Promotion gate

- [ ] **Vectorized result never promoted alone.** Every parameter set that survives
      the sweep was re-run event-driven, with a stated `execution_lag_bars`, before
      capital was committed.
- [ ] **Cost rates sourced.** `commission_bps` and `slippage_bps` come from a real
      fee schedule and a measured spread, not from the defaults. See
      `transaction-cost-analysis-tca-integration` and `execution-realistic-simulation`.

## Automated testing

- [ ] `python -m unittest discover -s skills/vectorized-vs-event-driven-backtest-tradeoffs/scripts -v`
      — 41 tests, 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
