# HKEX Order Validation — Pre-Flight Checklist

## Spread table
- [ ] Is the security's spread table taken from its **OMD-C Spread Table Code**, not inferred from its price or ticker?
- [ ] Are Parts A, B, D and E kept as **separate** tables? (A HK$5.00 equity ticks at 0.005; a HK$5.00 CBBC at 0.010.)
- [ ] Are band boundaries **upper-inclusive**? (`500.00` → 0.200, `500.20` → 0.500.)
- [ ] Does Part A run all the way to **9,995.00**, with 1.000 / 2.000 / 5.000 above 1,000 / 2,000 / 5,000?
- [ ] Does a price outside the table's range **raise**, rather than reusing the nearest band's tick?
- [ ] Does the table reflect Reduction of Minimum Spreads **Phase 1 (2025-08-04)** and **Phase 2 (2026-08-03)**?
- [ ] Has the table been reconciled against the current Second Schedule PDF this release?

## Price
- [ ] Is tick alignment tested with `Decimal` exact remainder, with **no tolerance**?
- [ ] Do prices reach the validator as `str`/`Decimal` rather than round-tripping through binary floats?
- [ ] Are NaN, infinity and non-numeric prices rejected?

## Stock code
- [ ] Are codes zero-padded to 5 digits (`00700`)?
- [ ] Are codes **longer than 5 digits**, non-numeric codes and `00000` rejected rather than padded?
- [ ] Is the HKD (`0XXXX`) / RMB (`8XXXX`) counter recorded on the audit trail?
- [ ] Is dual-counter eligibility confirmed against HKEX's Dual Counter Securities list, not just the leading digit?

## Quantity
- [ ] Is `board_lot_size` read from the security master — **never defaulted to 100**?
- [ ] Are `board_lot_size` values of `0` and negatives rejected before any `%` operation?
- [ ] Are **odd lots** (< 1 board lot) and **special lots** (> 1 board lot, non-multiple) distinguished and both kept off the auto-matching book?
- [ ] Is the **3,000 board lot** automatch cap enforced, and are larger parents sliced before submission?

## Reporting
- [ ] Does the caller read `violations` (all breaches) and not only `status` (highest-precedence breach)?
- [ ] Are rejections logged with the full audit line for post-trade review?

## Outside this module
- [ ] Are the **24-spreads** opening quotation rule and the **9-times-nominal-price** rule enforced elsewhere?
- [ ] Are session state, halts and VCM cooling-off checked before submission?
- [ ] Is ClOrdID uniqueness and order-state tracking handled by the OCG-C client?
- [ ] Has **OCG-C certification** been completed for the interface in use? (Unit tests are not certification.)
