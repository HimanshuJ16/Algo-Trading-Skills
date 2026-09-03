# Weather Derivatives Operations Checklist

## Contract master
- [ ] **Specification bound, not typed**: contract built via
      `WeatherDerivativeContract.from_spec(...)`, so `tick_value` and `currency` come
      from `CME_CONTRACT_SPECS`. Hand-entered multipliers are reserved for OTC swaps.
- [ ] **Multiplier and currency confirmed against the venue**: USD 20/point (CME US
      degree day), EUR 20/point (CME European HDD and CAT), JPY 2,500/point (CME
      Pacific Rim CAT). A USD-denominated CAT contract does not exist.
- [ ] **Station identified**: settlement station ID recorded (e.g. `KLGA` LaGuardia,
      `KORD` Chicago O'Hare) and matched to the contract, not to the nearest station.
- [ ] **Listing confirmed**: the city and contract month are currently listed on CME.
- [ ] **Entry index price recorded** for every futures position — P&L is undefined
      without it.
- [ ] **Payout cap set** on every sold OTC swap: `max_payout`, plus `max_loss` if the
      cap is asymmetric. `None` means uncapped; `0.0` means a genuine zero cap.

## Index accumulation
- [ ] **Temperature unit stated explicitly**, matching the observations.
- [ ] **Base temperature matches the contract's unit**: 65 °F for CME US, 18 °C for
      CME European HDD, none for CAT.
- [ ] **Station series quality-controlled**: no missing days, no non-finite values, no
      inverted $(T_{\min} > T_{\max})$ records. Any infill is explicit and recorded —
      a silently absorbed gap understates a degree-day index.
- [ ] **Negative CAT accepted**: no non-negativity check applied to a CAT index.

## Valuation
- [ ] **Structural breaks screened**: station relocations and instrument changes
      identified before fitting a trend, and handled as breaks rather than smoothed.
- [ ] **Record detrended** with `detrend_historical_indexes()` before burn analysis,
      targeting the contract season.
- [ ] **Fitted slope sanity-checked** against the station's documented history when it
      is large relative to the residual dispersion.
- [ ] **Burn analysis run over 20–30 detrended seasons**; the seasons used and the
      detrended series are archived with the result.
- [ ] **Discount factor applied** for anything but a short-dated contract.
- [ ] **Position sized on `worst_historical_payoff`**, not on the expected payoff.
- [ ] **Tail claims qualified**: `payoff_5th_percentile` reported as an empirical range
      figure, not as a VaR.
- [ ] **No Black-Scholes valuation** applied to a weather underlying.

## Settlement and credit
- [ ] **Futures P&L distinguished from settlement value**: P&L from
      `calculate_settlement_payoff` (entry-relative), notional from
      `final_settlement_value`.
- [ ] **Settled on the reported index**: cash settlement uses the index reported by
      Speedwell Settlement Services Ltd on the second Exchange Business Day after the
      contract month — never on a local recomputation.
- [ ] **Estimate reconciled against the reported index**, with any divergence
      investigated before settlement is instructed.
- [ ] **OTC exposure marked** against the ISDA credit support annex threshold, with
      collateral called when breached.
