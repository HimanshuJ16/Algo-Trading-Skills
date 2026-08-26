# Pre-Flight Checklist

- [ ] Is this a **strategy-based** margin account? (A portfolio-margin account is margined under FINRA Rule 4210(g) / OCC TIMS and none of these numbers apply.)
- [ ] Does every leg carry an explicit `expiration`, and do all legs share the same one? (Calendars and diagonals receive no offset here.)
- [ ] Does every short leg expire on or before the longest long leg, as FINRA Rule 4210(f)(2)(H) requires for spread treatment?
- [ ] Are leg quantities positive contract counts with direction in `action`, and are long and short counts matched? (An unmatched ratio leaves uncovered shorts.)
- [ ] Is `contract_multiplier` read from the contract specification rather than assumed to be 100? (Adjusted contracts after a split, merger or special dividend differ.)
- [ ] Is `underlying_pct` set for the option class — 0.20 equity, 0.15 broad-based index?
- [ ] Are premiums on the intended basis — current marks for a maintenance figure, trade prices for an initial figure?
- [ ] Is `status` reviewed, not just the headline saving? `NO_OFFSET_UNDEFINED_RISK`, `NO_OFFSET_MULTI_EXPIRY` and `NO_OFFSET_NAKED_REQUIREMENT_BINDS` all mean no capital was freed.
- [ ] Is `warnings` empty, and if not has each entry been resolved?
- [ ] Is `binding_constraint` understood — did the maximum potential loss bind, or the naked requirement?
- [ ] Has the un-offset requirement been checked as the **worst-case legging exposure**, i.e. can the account carry it if only some legs fill?
- [ ] Are the legs actually routed as a single combination order, not as separate orders?
- [ ] Has the estimate been reconciled against the broker's own requirement (house add-ons are common) before freed capital is redeployed?
- [ ] Are near-the-money short legs monitored for early assignment, which converts the combination into a stock position with a different requirement?
