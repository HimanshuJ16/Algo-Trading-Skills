# Workflows for JSE South Africa Equity Market Integration

1. **Alpha Code Normalization**:
   - Uppercase and strip the JSE alpha code (the instrument `Symbol`).
   - Validate the character class — ASCII alphanumeric, non-empty — not a fixed
     length. `S32` (South32) contains digits and `ETFSWX` is six characters;
     a "three uppercase letters" rule rejects real instruments.
2. **Structural Validation** (raise, do not report a status):
   - Side in `BUY`/`SELL`; trading segment in `ZA01`/`ZA02`/`ZA03`/`ZA04`/`ZA06`
     (`ZA11`/`ZA12` for NSX); trading session in the six published sessions.
   - Quantity a whole number greater than zero.
   - Static reference price finite and strictly positive **before** any division
     or band derivation.
3. **Tick Alignment in ZAC**:
   - Tick size is 1 ZAC for every instrument at every price level. Verify the
     limit price is a whole number of cents using integer arithmetic; a float
     tolerance would accept 85,500.0001 ZAC.
4. **Order Size Check**:
   - Reject quantities above the Maximum Order Size of 99,999,999 shares.
5. **Price Band Audit (rejects the order)**:
   - `ZA01`: reject outside $\pm 90\%$ of the static reference price.
   - Other segments: no published band, so enforce none.
6. **Circuit Breaker Assessment (does not reject the order)**:
   - Look up the (static, dynamic) tolerance for the segment/session pair;
     several pairs have none, which is not a pass and not a breach.
   - Breach when the deviation is **>=** the tolerance, measured against the
     static reference price and, where a last traded price is supplied, the
     dynamic reference price. The more restrictive takes precedence.
   - When no last traded price is available, record that the dynamic leg was
     skipped instead of reporting it as satisfied.
   - Classify a breach as a market-impact warning: the instrument moves into a
     5-minute Volatility Auction Call session and the aggressing remainder is
     booked (persistent TIF) or expired (non-persistent TIF).
7. **ZAR Notional Conversion**:
   - `equivalent_price_zar = price_zac / 100`;
     `notional_value_zar = price_zac * quantity / 100`.
8. **Audit Report Generation**:
   - Emit a structured `JseOrderReport`. Downstream routing branches on
     `is_rejected`, never on string comparison against a status, so that
     `VOLATILITY_AUCTION_RISK` — a valid, accepted order — is not mistaken for
     a rejection.
