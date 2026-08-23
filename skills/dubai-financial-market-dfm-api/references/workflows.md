# Workflows for Dubai Financial Market (DFM) API Integration

1. **NIN Investor Identification**:
   - Validate a 10-digit National Investor Number issued by Dubai CSD at DFM.
   - The engine places it in FIX Tag 1 (`Account`). This mapping is an unverified
     illustrative default — confirm it against DFM's member technical specification.

2. **Order Field Validation**:
   - Validate `side` against an explicit `BUY`/`SELL` allow-list. Never coerce an
     unrecognised value: defaulting to SELL inverts the order silently.
   - Validate quantity (positive integer), price (positive finite), currency
     (`AED`/`USD`), and the per-security band percentages.

3. **Tick Size Audit** (structure effective 6 April 2026):
   - `<1 → 0.001` · `1–9.99 → 0.01` · `10–49.98 → 0.02` · `50–99.95 → 0.05` ·
     `100 and above → 0.10`.
   - Under Rule 16.17(a)(ii) DFM specifies tick sizes by Circular, so the table is
     configurable; the final band must be open-ended so no price escapes a tick.
   - Compare integer tick counts rather than `price % tick`.

4. **Upper/Lower Price Limit Verification** (Rule 16.16):
   - Apply the per-security, asymmetric band from the applicable Circular. Default
     `-10%` / `+15%`.
   - Reject when the benchmark closing price is missing or non-positive; never skip
     the check.
   - Rule 16.16(c): no limits in a security's first Trading Session — opt in explicitly.
   - Rule 16.16(d): dual-listed issuers use the Foreign Market closing price as benchmark.

5. **FIX 4.4 Payload Construction**:
   - Build an SOH-delimited New Order Single with BodyLength (9) and CheckSum (10)
     computed per the FIX 4.4 standard, plus session fields 49/56/34/52 and OrdType 40.
   - Render the price at the tick's decimal precision so no binary floating-point
     artefact reaches the wire.
   - **Nothing is transmitted.** Only a venue Execution Report confirms receipt.
