# Pre-Flight Checklist

US federal income tax scope only.

## Completeness & integrity
- [ ] Are all trade records populated with mandatory fields (trade_id, symbol, side, qty, price, trade_date, cost_basis_usd)?
- [ ] Is `trade_id` unique across the whole record set?
- [ ] Do all dates parse as ISO-8601, and does every disposal fall on or after its acquisition?
- [ ] Is the audit run against an explicit `as_of` date so the output is reproducible?

## Holding period (IRC § 1222)
- [ ] Are `acquisition_date` and `disposal_date` recorded, rather than relying on a day count?
- [ ] Is long-term treatment applied only where disposal falls **strictly after** the one-year anniversary?
- [ ] Has every $366$-day ambiguous record been resolved with real dates instead of defaulted?

## Wash sales (IRC § 1091)
- [ ] Does every capital-account sell carry a recorded determination, including the negative ones?
- [ ] Have determinations made within 30 days of the sale been re-run after the window closed?
- [ ] Are wash sale checks suppressed for securities under a valid § 475(f) election?

## Lot identification (Treas. Reg. § 1.1012-1(c))
- [ ] Does every `SPECIFIC_ID` sell have an identification dated no later than settlement (T+1)?
- [ ] Have unsubstantiated specific-lot claims been recomputed on a FIFO basis?

## § 475(f) mark-to-market
- [ ] Is each `held_for_investment` security identified as such in the records **on the day acquired**?
- [ ] Are those investment securities still being tested for holding period and wash sales?

## Retention
- [ ] Does the retention clock run from **disposal**, never from the trade date?
- [ ] Are records for still-open positions excluded from every purge job?
- [ ] Is the configured `retention_years` understood as a firm policy default, not a statutory 7-year IRS minimum?
- [ ] Are records under legal, examination or litigation hold excluded from purge regardless of age?
- [ ] Has any pending purge of a not-yet-eligible record been stopped?
