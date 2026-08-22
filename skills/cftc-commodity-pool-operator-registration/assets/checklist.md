# Pre-Flight Checklist — CFTC 4.13(a)(3) Threshold Gate

## Gate placement

- [ ] Does the gate run *pre-trade*, on every commodity interest order, rather than as an end-of-day report?
- [ ] Do non-commodity instruments bypass the numerators while still feeding the liquidation value denominator?
- [ ] Is the verdict recomputed per order rather than cached between orders?

## Inputs

- [ ] Is liquidation value marked after unrealized profits and losses (not subscribed capital, not a stale NAV)?
- [ ] Does the margin aggregate include option premiums and the retail-forex minimum security deposit, not just futures initial margin?
- [ ] Is option notional computed as contracts × contract size × delta × strike, per 4.13(a)(3)(ii)(B)?
- [ ] Is the in-the-money amount of options that were in-the-money at purchase excluded from the 5% test?
- [ ] Is any netting limited to what the rule permits (same underlying commodity across DCMs/FBOTs; swaps on the same DCO), with gross used otherwise?
- [ ] Are exposure deltas passed with the documented sign convention (positive adds, negative releases; direction not encoded in the sign)?

## Decision logic

- [ ] Does the engine allow a trade when *either* the 5% margin test or the 100% notional test passes?
- [ ] Are risk-reducing trades allowed even when the pool is already outside both tests?
- [ ] Does a position sitting exactly on a threshold pass (the rule says "will not exceed" / "does not exceed")?
- [ ] Does invalid input (NaN, infinity, negative aggregate, over-release) block the trade rather than pass it?

## Audit and filings

- [ ] Is every decision persisted with both ratios, both projected aggregates, and the reason?
- [ ] Is a block escalated to compliance rather than auto-overridden in the order path?
- [ ] Has the 4.13(b) notice of exemption been filed with NFA before subscription agreements were delivered?
- [ ] Is the annual affirmation of that notice tracked and completed within 60 days of calendar year end (4.13(b)(4))?
- [ ] Are the non-quantitative conditions — (a)(3)(i) offering, (a)(3)(iii) participant eligibility, (a)(3)(iv) marketing — owned by a named control outside this engine?
