# Pre-Flight Checklist

- [ ] Is a 10-digit National Investor Number (NIN) populated, and is its FIX tag mapping confirmed against DFM's member technical specification (not assumed)?
- [ ] Is the order currency correct for the venue — AED for DFM, and AED *or* USD for Nasdaq Dubai depending on the board?
- [ ] Is the tick table sourced from the current DFM circular, and does it include the AED 100+ → 0.10 bracket with an open-ended final band?
- [ ] Is the tick check done on integer tick counts rather than floating-point modulo?
- [ ] Are the Upper/Lower Price Limits taken per security from the applicable DFM circular, and is the band treated as asymmetric rather than ±10%?
- [ ] Does a missing or non-positive benchmark price cause a rejection rather than skipping the band check?
- [ ] Is `is_first_trading_session` used only for genuinely unbanded securities (Rule 16.16(c)), and are dual-listed issuers benchmarked to the foreign closing price (Rule 16.16(d))?
- [ ] Is an unrecognised order side rejected rather than coerced to SELL?
- [ ] Are free-text FIX fields (`cl_ord_id`, `symbol`) rejected when they contain SOH or `=`, so caller input cannot forge FIX fields?
- [ ] Are FIX BodyLength (9) and CheckSum (10) verified against the spec, with SOH delimiters rather than pipes?
- [ ] Is it understood that a built payload is NOT an acknowledgement, and that only a venue Execution Report confirms the order reached DFM?
