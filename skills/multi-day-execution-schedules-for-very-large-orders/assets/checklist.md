# Pre-Flight Checklist — Multi-Day Parent Order

## Inputs

- [ ] `total_parent_quantity` is in **shares**, not notional.
- [ ] Parent quantity is genuinely too large for one session (materially above the
      participation cap) — otherwise this is a single-day scheduling problem.
- [ ] ADV window is recorded with the order, and matches the window any applicable rule
      specifies (four calendar weeks for 10b-18 ADTV; 20 trading days for EU 2016/1052).
- [ ] `volatility_daily_pct` is **daily**, on the same frequency as ADV.
- [ ] `shares_outstanding` supplied, or permanent impact is knowingly unavailable.

## Participation cap

- [ ] The cap is a deliberate house limit, documented with its rationale — not the 10%
      default carried through unexamined.
- [ ] Checked whether a statutory cap applies (issuer buy-back, affiliate/restricted resale,
      EU buy-back programme). If so, it is encoded in `max_daily_participation_pct`.
- [ ] The cap was **not** widened merely to fit a deadline.

## Horizon

- [ ] Minimum feasible horizon computed and compared against the deadline.
- [ ] If a tilted trajectory is wanted, `target_horizon_days` exceeds the minimum — at the
      minimum, the cap fixes every slice and all profiles are flat.
- [ ] Session indices mapped onto the venue calendar; holidays and half-days handled, with
      the cap scaled down for short sessions.

## Cost and risk

- [ ] Temporary impact, permanent impact and overnight risk all read in bps of the parent
      notional so they are comparable.
- [ ] Permanent impact confirmed **not** to move with the horizon.
- [ ] Overnight risk understood as a **1σ** dispersion, not a maximum or a VaR figure.
- [ ] At least two candidate horizons compared, and the chosen one recorded with its reason.
- [ ] Impact coefficients either recalibrated for this market, or the ATHL fit's
      2001–2003 US large-cap provenance explicitly accepted as an approximation.

## Schedule integrity

- [ ] Daily targets sum to the parent quantity.
- [ ] No session's target exceeds the cap — re-checked **after** lot rounding.
- [ ] Trajectory shape matches the requested profile (monotone in the expected direction).

## Operation

- [ ] Each session's target is handed to an intraday algo; this schedule is a budget, not
      an order.
- [ ] Re-plan cadence agreed: rerun with actual remaining quantity and refreshed ADV and
      volatility after each session, rather than rolling shortfalls forward.
- [ ] Escalation path defined for a halt, a volume collapse, or a volatility regime shift
      mid-horizon.
