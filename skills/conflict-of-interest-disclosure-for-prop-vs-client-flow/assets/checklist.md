# Pre-Flight Checklist

## Scope
- [ ] Is the instrument an NMS stock or OTC Equity Security? (Rule 5320 reaches nothing else.)
- [ ] Is the firm a FINRA member, and is this US order flow? Non-US flow is governed by different rules — no 10,000-share carve-out exists in the EU/UK.

## Direction of the price test
- [ ] For a held customer **BUY** limit, does the gate flag proprietary purchases **at or below** the limit?
- [ ] For a held customer **SELL** limit, does it flag proprietary sales **at or above** the limit?
- [ ] Is the Rule 5320.06 minimum price improvement increment applied on top, so a sub-penny improvement over a customer limit still blocks?
- [ ] Are prices compared in `Decimal`, not binary floats, given the rule turns on exact equality with the limit?

## Coverage
- [ ] Are **all** matching unexecuted client orders evaluated before the prop order is approved — not just until the first exception is found?
- [ ] Does the audit record list every conflict and every exception applied?
- [ ] Is the client-order snapshot current at the moment of the check?

## Fail-closed behaviour
- [ ] Are unrecognised sides, non-finite or non-positive prices and non-positive quantities rejected rather than falling through to "no conflict"?
- [ ] Does an unparseable client order block the prop order instead of being skipped?
- [ ] Does the routing path branch on `is_approved`, or use `enforce_prop_order`?

## Exceptions
- [ ] Is `barriers_effective` sourced from an attested barrier inventory, not inferred from the `info_barrier_id` string alone?
- [ ] For OTC Equity Securities, is the no-knowledge exception withheld from the market-making desk (Rule 5320.02)?
- [ ] Is the Rule 5320.01 exception conditioned on the written disclosure being given at account opening **and annually**, and on the customer **not** having opted in?
- [ ] Is the large-order test `>= 10,000 shares` **AND** `>= $100,000` — never OR?
- [ ] Is institutional status determined against the Rule 4512(c) definition ($50 million total assets for the catch-all category)?
- [ ] Is the round lot for the odd-lot exception correct for the security, not assumed to be 100?

## Tagging and audit
- [ ] Is `OrderCapacity(528)` populated on 100% of outbound messages (not deprecated `Rule80A(47)`)?
- [ ] Are written order handling procedures documented and periodically reviewed (Rule 5320.07)?
- [ ] When the firm does trade at a satisfying price, is the customer order actually executed up to size at the same or better price?
