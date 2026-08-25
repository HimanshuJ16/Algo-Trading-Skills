# Pre-Flight Checklist — Exchange Fee Tier & Rebate Analysis

## Schedule ingestion
- [ ] Rates transcribed into the module sign convention (negative = rebate credited, positive = fee charged)? Cboe's parenthesised `($0.0027)` is a **rebate**, i.e. `-0.0027`.
- [ ] Schedule taken from the venue's **currently published** fee schedule, not from memory or a cached copy?
- [ ] Explicit tier defined at threshold `0`?
- [ ] Percentage-of-consolidated-volume criteria converted to absolute share thresholds, with the ADV forecast that conversion depends on recorded?
- [ ] Any tier requiring multiple simultaneous criteria flagged as out of scope for this module?

## Venue classification
- [ ] Pricing model confirmed as maker-taker or inverted **against the venue's published schedule**, not from a remembered classification? (Cboe EDGA has been maker-taker since 1 Nov 2024.)
- [ ] Engine constructed without raising — i.e. the schedule does not contradict the declared pricing model?
- [ ] Construction warnings read? A maker-taker tier that charges makers earns passive flow nothing.

## Tier qualification basis
- [ ] Basis chosen deliberately: `PRIOR_PERIOD` for US NMS stocks (Reg NMS Rule 610(d), in force since 2 Feb 2026), `ROLLING_CURRENT` only for crypto or non-US venues?
- [ ] Under `PRIOR_PERIOD`, is `qualifying_volume_shares` sourced from a **completed prior period** rather than from the volume being priced?
- [ ] Does any surrounding logic still assume current-month volume improves the current month's rate? On a US equity venue it does not.

## Cost calculation
- [ ] Net cost read as a signed figure, with negative understood as net rebate capture?
- [ ] On an inverted venue, is `maker_side_cost_usd` / `taker_side_cost_usd` being read rather than the maker-taker-named legacy fields?
- [ ] Do downstream consumers tolerate a negative net cost without assuming costs are non-negative?

## Tier jump decision
- [ ] Decision based on `net_tier_jump_benefit_usd`, not on gross savings?
- [ ] `tier_jump_benefit_period` checked — does the benefit land this period or next?
- [ ] `incremental_maker_fraction` set to reflect the actual mix of the volume you would add, if it differs from current flow?
- [ ] Negative gross savings recognised as a genuinely worse tier rather than assumed to be zero?
- [ ] Adverse selection and market impact on the incremental volume assessed **outside** this module before committing to chase a tier?

## Compliance & reporting
- [ ] `check_reg_nms_access_fee_cap()` run for US NMS venues, and clean against the cap in force ($0.0030/sh)?
- [ ] Schedule pre-tested against the amended $0.0010/sh cap (compliance date first business day of November 2027, previously extended twice — re-verify)?
- [ ] `report.warnings` read and each entry either actioned or explicitly accepted?
- [ ] Routing decision cross-checked against fill probability and TCA rather than taken on fee economics alone?
