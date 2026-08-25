# Pre-Flight Checklist — Early Exercise / Assignment Risk

## Inputs
- [ ] Is every short option marked at the **bid** (not the mid or last trade)?
- [ ] Is `exercise_style` sourced from contract reference data, not inferred from the settlement method?
- [ ] Is `days_to_ex_div` left at `+inf` (not `0`) when no dividend is scheduled?
- [ ] Is `contracts_qty` a positive count and `contract_multiplier` correct for the contract?
- [ ] Is `same_strike_put_price` supplied wherever available, so the exact test replaces the conservative screen?
- [ ] Is `risk_free_rate` set deliberately rather than left at the `0.0` default?

## Screening
- [ ] Are short calls audited against every upcoming ex-dividend date **whose ex-date falls before expiration**?
- [ ] Is extrinsic value computed for all short positions, with below-parity quotes flagged rather than silently clamped?
- [ ] Are ITM short calls *and* puts checked against the parity threshold independently of any dividend?
- [ ] Does the parity threshold scale with strike, so it stays meaningful on high-notional contracts?
- [ ] Are `data_quality_flags` reviewed on every run, not just the risk level?

## Interpretation
- [ ] Is `assignment_risk_score` being read as an ordinal severity score and **not** as a probability of assignment?
- [ ] Is `exercise_test_used` checked before treating a hit as a verdict (`EXTRINSIC_SCREEN` over-flags by design)?
- [ ] Is the dividend liability ($D \times$ contracts $\times$ multiplier) sized against the account's tolerance?

## Action
- [ ] Are flagged short calls closed or rolled **before the clearing member's exercise cutoff on the last cum-dividend session**?
- [ ] Is the broker's early-exercise cutoff for an ordinary session known and documented (it is not the 5:30 p.m. ET expiration-day deadline)?
- [ ] Is the full audit report persisted per run for post-assignment forensics?
- [ ] Is expiry-day pin risk handled separately, rather than assumed to be covered by this screen?
