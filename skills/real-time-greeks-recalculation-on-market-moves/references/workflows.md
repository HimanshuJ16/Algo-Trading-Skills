# Workflows for Real-Time Greeks Recalculation on Market Moves

## 1. Validate the tick and every leg before publishing anything

- Reject a non-finite or non-positive `spot`; a blank `symbol` or `underlying_symbol`;
  a non-positive `multiplier`, `strike`, `implied_vol` or `time_to_expiry_years`; an
  `option_type` outside `{CALL, PUT}`; a non-finite `position_qty`, `risk_free_rate`
  or `dividend_yield`; and a duplicate `symbol` within one tick.
- **Why a NaN spot is the dangerous one.** `abs(nan) > threshold` evaluates to
  `False`. An unvalidated NaN therefore does not look like an error — it looks like a
  *small move*, never trips the revaluation trigger, and pins the entire book on
  cached Greeks for as long as it persists.
- **Reject the whole tick, not the offending leg.** A partial Greeks snapshot is a
  risk number with an unknown fraction of the book missing from it, which is worse
  than no number because it looks like one.
- **Validate `option_type`; do not branch on it.** `if t == "CALL": … else: # PUT`
  turns `"C"`, `"Call "` and `""` into puts and flips the sign of delta. A mis-signed
  delta reads downstream as a legitimate position on the other side of the book.
- **Do not clamp expiry.** `T = max(1e-4, T)` reports a confident delta for a contract
  that has none. Raise, and let the caller decide what an expired leg means.
- **`multiplier` has no default.** It is deliverable units per contract from the
  contract master — see `references/standards.md` for why 100 is unsafe even for US
  equity options.

## 2. Restrict the tick to its own underlying

- A tick is one price for one name. Positions on any other underlying are excluded
  from the recalculation and from the nets, and counted in
  `positions_skipped_other_underlying`.
- **This is the structural fix for stale-spot Greeks.** If spot lives on the position
  rather than on the tick, a book eventually nets Greeks computed at several different
  prices for the same name, and nothing in the output says so. One spot per tick makes
  that state unrepresentable rather than merely discouraged.
- Match the underlying case-insensitively so `aapl` and `AAPL` are one book.
- Cross-underlying netting, dollar-normalisation and limit auditing are
  `options-greeks-real-time-portfolio-aggregation`'s job.

## 3. Test against the anchor — the last full revaluation

$$\text{drift} = \frac{|S_t - S_{\text{anchor}}|}{S_{\text{anchor}}}$$

**The anchor is the spot of the last full revaluation. It is never the previous tick.**
This is the whole design. With a last-tick baseline:

| Tick | Move vs last tick | Move vs anchor | Last-tick policy | Anchored policy |
|---|---|---|---|---|
| 100.00 | — | — | reprice (no anchor) | reprice (no anchor) |
| 100.40 | +0.400% | +0.400% | step | step |
| 100.80 | +0.398% | +0.800% | **step** | **reprice** |

Extended: sixty consecutive $+0.1\%$ ticks carry spot to $106.18$ with **zero**
revaluations under a last-tick baseline, because every individual step is "small". The
book is carried that entire $+6.2\%$ on a frozen gamma, and nothing in the output
indicates it.

Reprice when any of the following fires, and record which one, so the reasons observed
in production can be used to calibrate:

| Trigger | Condition | Why it exists |
|---|---|---|
| `NO_ANCHOR` | first sight of the contract | nothing to step from |
| `NEAR_EXPIRY` | $T \le$ `near_expiry_years` | gamma is unbounded and delta discontinuous through the strike; the expansion is the wrong *shape*, not merely imprecise |
| `SPOT_MOVE_THRESHOLD` | drift $>$ `full_recalc_spot_move_pct` | the expansion's residual grows with $h$ |
| `IV_MOVE_THRESHOLD` | $\|\sigma_t - \sigma_{\text{anchor}}\| >$ `full_recalc_iv_move_abs` | $\Gamma$ and $\nu$ are functions of $\sigma$; a vol repricing on a flat tape leaves every cached Greek stale and a spot-only trigger silent |
| `ANCHOR_AGE` | age $>$ `max_anchor_age_seconds` | theta and charm move the Greeks with no tick at all, so an illiquid name's Greeks are as old as its last print |
| `TAYLOR_DELTA_OUT_OF_BOUNDS` | stepped $\Delta \notin [0,1]$ (call) or $[-1,0]$ (put) | an impossible Greek is worse than the reprice it should have cost |
| `CALLER_FORCED` | caller asked | scheduled sweeps, post-gap resync, EOD marks |

- **Order the tests by validity, not by cost.** Expiry precedes spot: inside the pin
  horizon it does not matter how small the move was.
- **Reset the anchor on a feed gap or session boundary.** After a gap the anchor's
  provenance is unknown, and a stale anchor is worse than a missing one — a missing
  anchor forces a revaluation, a stale one suppresses it.
- **Reject an out-of-order tick.** A late-arriving earlier print would advance the
  anchor backwards and leave the book permanently mismarked.

## 4. Step, or reprice and re-anchor

**Step** (cheap path):

$$\Delta \leftarrow \Delta_0 + \Gamma_0 h, \qquad \Delta V \approx \Delta_0 h + \tfrac{1}{2}\Gamma_0 h^2$$

$\Gamma$, $\nu$ and $\Theta$ are carried forward frozen. Their own drift — speed,
vanna, charm — is second order over a sub-threshold move, which is precisely what the
triggers in step 3 bound. The anchor is **not** touched.

**Reprice** (accurate path): full Black-Scholes-Merton with continuous dividend yield
(formulas and units in `references/standards.md`), then re-anchor spot, implied vol,
expiry, timestamp, price and all four Greeks **in one update**. A half-updated anchor
— new spot against old Greeks, say — is a permanent, silent bias that no later tick
corrects.

## 5. Emit the report and feed the error back

`RealTimeGreeksReport` carries the ticked underlying's nets (delta units, dollar delta,
gamma units, dollar gamma, vega per point, theta per calendar day), the per-position
results with their method and trigger, `positions_full_revalued` /
`positions_taylor_updated` / `positions_skipped_other_underlying`,
`max_spot_drift_from_anchor_pct`, and the sorted set of triggers observed.

- Sum with `math.fsum`, not repeated `+=`, so ordering cannot change the answer.
- Scale by the deliverable exactly as `options-greeks-real-time-portfolio-aggregation`
  does, so the two skills' numbers are directly comparable.
- **Calibrate on `spot_taylor_value_error_per_unit`.** Every revaluation reports the
  realised residual of the delta-gamma step it replaced, evaluated at the anchor's vol
  and expiry so the comparison isolates spot. Negligible error means the threshold is
  too tight and CPU is being burned; material error means the published delta was
  wrong between revaluations. This is the only honest way to set the threshold —
  a rule of thumb is not evidence.
- Route the output to something that acts. This engine observes: it does not hedge
  (`greeks-based-portfolio-hedging-automation`) and does not halt trading
  (`kill-switch-and-drawdown-circuit-breakers`).
