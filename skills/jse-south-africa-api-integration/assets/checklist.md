# Pre-Flight Checklist

- [ ] Is the JSE alpha code validated on **character class**, not on a fixed three-letter length (`S32`, `ETFSWX` are valid)?
- [ ] Is the order price specified in **ZAC South African Cents** (ZAR 1 = 100 ZAC), not Rand?
- [ ] Is the price a **whole number of cents** — the JSE tick size is 1 for every instrument, with no price-tiered ladder?
- [ ] Is the tick check done in integer arithmetic rather than with a float tolerance?
- [ ] Is the quantity a whole number greater than zero and within the **99,999,999 Maximum Order Size**?
- [ ] Is `reference_price_zac` the **static** reference price (previous close or last auction price) — not the last traded price?
- [ ] Is the static reference price validated as finite and strictly positive before it is used as a divisor?
- [ ] Is the correct **trading segment** supplied (`ZA01`–`ZA06`, `ZA11`/`ZA12`)?
- [ ] Is the correct **trading session** supplied — circuit breaker tolerances differ per session, not just per segment?
- [ ] Is the `ZA01` $\pm 90\%$ **price band** enforced, and no band invented for the other segments?
- [ ] Is a **circuit breaker breach treated as a volatility-auction warning, not a rejection**?
- [ ] Is a breach evaluated as **>= tolerance** (inclusive), against both the static and dynamic reference prices?
- [ ] Is a missing last traded price recorded as "dynamic breaker not evaluated" rather than as a pass?
- [ ] Does downstream routing branch on `is_rejected` rather than string-matching the status?
- [ ] Is the ZAR equivalent notional calculated for portfolio and risk aggregation?
- [ ] Have the circuit breaker and price band tables been re-verified against the current Volume 00E?
