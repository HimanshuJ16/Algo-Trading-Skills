# Pre-Flight Checklist

## Contract data
- [ ] Are front-month and next-month volume and open interest feeds active, and is the open interest the value the exchange has actually published (it lags by one session)?
- [ ] Are all day counts business days, from the same reference session, for both contracts?
- [ ] Is `contract_multiplier` set to the product's real point value, and identical on both legs?

## Delivery risk
- [ ] Is the product physically delivered? If so, is `is_physically_delivered=True` **and** `days_to_first_notice` supplied?
- [ ] Has First Notice Day been read from the current rulebook chapter rather than assumed to follow Last Trading Day? (For CBOT grains FND is the last business day of the month *preceding* delivery — before LTD, not after.)
- [ ] Is `min_days_to_first_notice` calibrated with enough slack for your own operational cut-offs, not just the exchange's?

## Trigger configuration
- [ ] Is the days-to-expiration cutoff calibrated for this product's roll window rather than left at the default 5?
- [ ] Are the volume and open-interest crossover triggers set deliberately, and is any multi-session confirmation applied upstream?

## Order construction
- [ ] Has the venue's calendar-spread quoting convention been confirmed for **this product** (CME quotes FX far-minus-near, the standard `SP` listing nearby-minus-deferred)?
- [ ] Does `spread_side` match what the venue expects for the direction you intend, so the roll closes the position rather than doubling it?
- [ ] Is the tradable spread instrument resolved from the venue security definition rather than the `spread_symbol` label?
- [ ] Is the roll executed as one exchange calendar spread rather than two legs?

## Monitoring
- [ ] Are `ROLL_TOO_LATE_ESCALATE` and non-`NONE` `delivery_risk_level` values alerted on, not just logged?
- [ ] Is `estimated_roll_cost` fed into strategy P&L attribution, with fees and spread crossing added on top?
