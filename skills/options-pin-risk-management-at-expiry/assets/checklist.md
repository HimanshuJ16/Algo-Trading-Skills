# Pre-Flight Checklist — Options Pin Risk at Expiry

## Clock
- [ ] Is `hours_to_trading_close` measured against the **close of trading in the option** (4:00 p.m. ET for standard US equity options), not against the 11:59 p.m. ET contract expiration time?
- [ ] Is **your broker's** exercise cutoff known and written down? 5:30 p.m. ET (FINRA Rule 2360(b)(23)(A)) is the outer limit members may not exceed — members routinely set earlier ones, and theirs is the deadline that binds you.
- [ ] Is the expiration Friday's time zone handled as ET (Eastern *Daylight* Time from March to November), not hard-coded as EST?

## Inputs
- [ ] Is `spot_price` the official closing price, with `price_is_official_close=True`? If not, is every moneyness verdict being read as provisional?
- [ ] Is `contract_multiplier` sourced from reference data rather than assumed to be 100? Check for an OCC adjustment memo on any name with a recent split, spin-off or merger.
- [ ] Is `settlement_type` correct? Cash-settled index options have no share assignment to be uncertain about.
- [ ] Is `option_type` populated per leg — and is the engine actually reading it, so calls and puts at the same distance do not receive identical verdicts?
- [ ] Are `position_qty` values signed, non-zero, and whole numbers of contracts?

## Screening
- [ ] Is pin distance calculated in **both** percent and absolute currency, with `pin_distance_abs_usd` set on low-priced underlyings where 1% is a few cents?
- [ ] Is signed moneyness compared against the **\$0.01** OCC exercise-by-exception threshold on a rounded value, so a close exactly on the boundary is not misclassified?
- [ ] Are **out-of-the-money shorts** in the pin band flagged? A holder can file a contrary exercise advice to exercise them.
- [ ] Are **in-the-money shorts** treated as uncertain rather than as certain assignments? The advice can also cancel the automatic exercise.
- [ ] Are `data_quality_flags` reviewed on every run, not just the status?

## Exposure
- [ ] Is potential share delivery reported as a **signed share delta**, so the direction of the resulting position is unambiguous?
- [ ] Is funding sized on `assignment_cash_usd` (at the **strike**), not on the share notional at spot?
- [ ] For a DNE-eligible long, is the intrinsic that would be forfeited compared against the size of the delivery it avoids?

## Portfolio
- [ ] Has the **whole expiring book** been run through `audit_portfolio_pin_risk`, not just the positions someone remembered to check?
- [ ] For every pinned short leg, is the paired long leg of the **same option type**, in the money beyond \$0.01, **and** outside the pin band? An out-of-the-money long leg delivers nothing, a long leg that is itself pinned is equally uncertain, and a long call does not cover a short put.
- [ ] Is leg pairing netted in **shares** rather than contract counts? Legs with different multipliers (mini contracts, adjusted contracts) are not comparable by contract count.
- [ ] Is the reported min/max share range — not just the status — the number taken to the risk meeting?
- [ ] Are inconsistent spot prices across legs of one underlying resolved before acting on the netted exposure?

## Action
- [ ] Is every pinned short closed or rolled **before trading in the option ceases**, not before the exercise deadline?
- [ ] Once trading has closed, is residual exposure handled as `POST_CLOSE_EXPOSURE_REVIEW` rather than by an unexecutable close order?
- [ ] Is the full audit report persisted per run for Monday-morning post-assignment forensics?
