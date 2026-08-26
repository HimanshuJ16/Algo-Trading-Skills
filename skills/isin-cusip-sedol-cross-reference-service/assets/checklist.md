# Pre-Flight Checklist

## Algorithm correctness

- [ ] ISIN: characters expanded to digits **before** Luhn is applied, and Luhn runs over
      the expanded string (a letter becomes two digits and shifts every doubling position
      after it)?
- [ ] CUSIP: double-add-double over characters 1–8, 1-indexed even positions doubled?
- [ ] SEDOL: plain weighted sum $(1, 3, 1, 7, 3, 9)$ with **nothing doubled** — not Luhn?
- [ ] FIGI: double-add-double over characters 1–11, offset from ISIN as X9.145 requires —
      and *not* the ISIN routine reused?
- [ ] Each algorithm verified against an independently sourced value (a standard's worked
      example or a published real identifier), not against the implementation's own formula?

## Syntax rules

- [ ] SEDOL character set restricted to `0-9` and consonants — `E`, `I`, `O`, `U` rejected?
- [ ] FIGI syntax enforced: consonants in positions 1–2, `G` in position 3, no vowels in
      4–11, and the reserved prefixes `BS`/`BM`/`GG`/`GB`/`VG` excluded?
- [ ] Every check-digit position constrained to `[0-9]` **in the pattern**, so no code path
      calls `int()` on a character that could be a letter?
- [ ] CUSIP `*`, `@`, `#` (values 36/37/38) handled, with PPN CUSIPs understood to be
      non-embeddable in an ISIN?

## Classification and lookup

- [ ] Type inferred from full syntax + check digit, never from length alone (a FIGI is also
      12 characters and also starts with two letters)?
- [ ] `identifier_type` passed wherever the source feed labels the column?
- [ ] Ambiguous strings — e.g. `KYG875721634`, valid as both ISIN and FIGI — reported in
      `candidate_types` rather than silently resolved?
- [ ] Lookup scoped to the index for the resolved type, so a CUSIP query cannot match some
      other row's ticker?
- [ ] Whitespace and hyphens stripped for structured matching, but preserved for tickers
      (`BRK.B`)?
- [ ] Corrupted (`INVALID_CHECKSUM`) distinguished from unknown (`IDENTIFIER_NOT_FOUND`)?
- [ ] `checksum_applied` consulted before reading `is_checksum_valid` — a ticker has no
      check digit to validate?

## Master data integrity

- [ ] Every row's own ISIN/CUSIP/SEDOL/FIGI re-validated at load, and each resolves back to
      that row?
- [ ] `validate_master_data()` empty, or every reported problem triaged?
- [ ] Duplicate identifiers across rows investigated as upstream merge errors?
- [ ] Persistent joins keyed on the **FIGI**, with the ticker carried as an attribute
      (`FB` → `META` moved the ticker, not the FIGI)?
- [ ] Cross-listed issues modelled one-SEDOL-per-market, not one SEDOL per security?

## Boundaries

- [ ] Understood that a passing check digit proves typing, **not** existence — identifiers
      confirmed against the issuing agency or the OpenFIGI mapping API before production
      routing?
- [ ] Non-string and blank queries rejected explicitly rather than crashing the ingest loop?
- [ ] CUSIP redistribution licensing considered separately from check-digit validation?
