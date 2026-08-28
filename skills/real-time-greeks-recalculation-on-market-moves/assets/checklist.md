# Pre-Flight Checklist

## The anchor (read this one first)

- [ ] Is the spot-move test measured against the spot of the **last full revaluation**, never the previous tick? (A last-tick baseline lets 60 consecutive $+0.1\%$ ticks travel $+6.2\%$ with **zero** revaluations.)
- [ ] Is the anchor replaced **atomically** — spot, implied vol, expiry, timestamp, price and all four Greeks in one update?
- [ ] Is the anchor reset on a feed gap, a session boundary and a position close? (A stale anchor suppresses the revaluation a missing one would force.)
- [ ] Is an out-of-order tick rejected rather than applied backwards?

## Triggers

- [ ] Can an **implied-vol** move force a revaluation with no spot move at all? ($\Gamma$ and $\nu$ are functions of $\sigma$.)
- [ ] Is there a **staleness cap** on anchor age? (Theta and charm move the Greeks with no tick at all.)
- [ ] Is every position **fully revalued** inside the near-expiry horizon, regardless of how small the move was?
- [ ] Does a stepped delta outside $[0,1]$ (call) or $[-1,0]$ (put) force a revaluation instead of being published?
- [ ] Is expiry checked **before** the spot threshold, so validity beats cost?
- [ ] Does every revaluation record **which** trigger fired, so the thresholds can be calibrated from production?

## Inputs

- [ ] Is `multiplier` read from the contract master for every position rather than defaulting to 100? (An OCC-adjusted contract delivering 5 shares scaled by 100 overstates its risk $20\times$.)
- [ ] Is there exactly **one spot per tick per underlying**, rather than a spot field on each position?
- [ ] Are positions on other underlyings **excluded**, never repriced at this underlying's spot?
- [ ] Is `dividend_yield` supplied for yielding underlyings? (Call delta carries $e^{-qT}$; assuming $q=0$ biases the whole book in one direction.)
- [ ] Are `implied_vol` and `time_to_expiry_years` refreshed by the caller on each tick?
- [ ] Is a monotonic `tick_timestamp_s` supplied? (Without it the staleness trigger cannot fire.)

## Validation

- [ ] Does a NaN/Inf spot **raise**? (`abs(nan) > threshold` is `False`, so a NaN reads as a *small move* and pins the book on cached Greeks.)
- [ ] Does `time_to_expiry_years` $\le 0$ raise rather than being clamped to `max(1e-4, T)`?
- [ ] Is `option_type` validated against `{CALL, PUT}` rather than branched on with an `else: # PUT`? (`"C"` would sign-flip delta.)
- [ ] Does one bad leg reject the **whole tick**, leaving no partial snapshot and no new anchors?
- [ ] Is a duplicate symbol within one tick rejected?
- [ ] Are zero and negative config thresholds rejected at construction? (A silently disabled trigger is the worst outcome.)

## Output and calibration

- [ ] Are nets summed order-independently (`math.fsum`)?
- [ ] Are the Greeks scaled by the **deliverable**, matching `options-greeks-real-time-portfolio-aggregation` so the two skills' numbers are comparable?
- [ ] Is `spot_taylor_value_error_per_unit` actually monitored, and are the thresholds calibrated from it rather than from a rule of thumb?
- [ ] Is the threshold justified by a **measured** revaluation cost on the target host, not an assumed one?

## Interpretation

- [ ] Is the output understood as only as fresh as the vol surface and expiry fed in? (Stepping quickly off a stale surface is a stale risk number, delivered promptly.)
- [ ] Is theta read as one **calendar** day, and vega as one **vol point**?
- [ ] Is the model's scope understood — European exercise, continuous dividend yield, vol taken as given per strike?
- [ ] Is the report routed to something that acts (a hedger or a circuit breaker)? This engine only observes.
