# Pre-Flight Checklist — Order Book Microstructure Signal Research

Sign off before quoting an IC to anyone who might size a position on it.

## Input integrity

- [ ] Series is a **single instrument**, time-sorted, with no crossed books and no non-finite values.
- [ ] Prices are strictly positive (simple mid-to-mid returns are undefined otherwise).
- [ ] Consolidated / NBBO feeds were cleaned of cross-venue ordering artifacts **before** the audit.
- [ ] `degenerate_depth_ticks` was read: zero-depth ticks are feed gaps or session boundaries, not zero imbalance.

## Formula correctness

- [ ] Order Flow Imbalance implements **all six** branches of $e_n$, including $-q^B_{n-1}$ when the best bid falls and $+q^A_{n-1}$ when the best ask rises.
- [ ] Verified: bid 150.00/900 → 149.90/10 with the ask unchanged gives `ofi == -900.0`, not `0.0`.
- [ ] `micro_price_dev == (voi / 2) * spread` holds to float tolerance.
- [ ] Understood that `micro_price` is the **weighted mid**, not Stoikov's martingale micro-price.
- [ ] `ofi_window_ticks` reflects the intended variable: 1 = per-event $e_n$, >1 = the published interval-summed $OFI_k$.

## No fabricated observations

- [ ] Index 0 (`is_event_observed=False`) is excluded from the research sample.
- [ ] Rolling-window warm-up rows (`is_window_complete=False`) are excluded.
- [ ] Forward returns use **unrounded** endpoints on both sides.
- [ ] `forward_horizon_ticks >= 1` and is at least as long as the tick-to-trade path the signal would be traded through.

## Statistical honesty

- [ ] `effective_observations` (= `observations // forward_horizon_ticks`) was read, not `observations`.
- [ ] `effective_observations >= 30` before treating any verdict as an approval.
- [ ] `INSUFFICIENT_SAMPLES` was not read as `WEAK_SIGNAL`, or vice versa.
- [ ] The t-statistic is understood as a conservative overlap discount, **not** a HAC (Newey-West / Hansen-Hodrick) estimator.
- [ ] `hit_ratio_pct` was read alongside `directional_predictions` and `flat_or_neutral_ticks`.
- [ ] `CONSTANT_SPREAD_COLLINEARITY` checked: `ic_micro_price_dev_return` and `ic_voi_forward_return` are not being reported as two agreeing signals.
- [ ] `IC_SIGN_INVERTED` investigated as a bid/ask mapping or alignment bug before being interpreted as a contrarian edge.

## Claims discipline

- [ ] The 65% $R^2$ from Cont/Kukanov/Stoikov (2014) is **not** cited as evidence of forward predictive power — it is a contemporaneous same-interval regression on 50 S&P 500 stocks, April 2010, 10-second bins.
- [ ] `MIN_IC_FOR_ALPHA`, `MIN_HIT_RATIO_PCT` and `MIN_EFFECTIVE_OBSERVATIONS` are documented downstream as this skill's engineering defaults, not published standards.
- [ ] Economic viability (spread, fees, queue position, latency) assessed separately before any capital allocation.
