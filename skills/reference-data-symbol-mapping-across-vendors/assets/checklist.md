# Pre-Flight Checklist

## Canonical symbol

- [ ] Canonical symbol keyed on something that does not move on a rename (FIGI or an
      internal surrogate), with the ticker carried as an attribute — `FB` → `META` moved
      the ticker, not the CUSIP or the listing?
- [ ] Identifiers validated at the ingest boundary before registration? This table stores
      opaque strings and cannot tell a corrupted ISIN from a good one.

## Registration

- [ ] `vendor_symbol` stored **exactly** as the vendor writes it, normalisation applied to
      lookup keys only?
- [ ] Every window whose dates are known carried as `effective_from` / `effective_to`,
      with `effective_to` understood as **exclusive**?
- [ ] Exactly one entry per (canonical, vendor, overlapping window) marked
      `is_primary=True`, and the alternates — Bloomberg composite vs primary-exchange
      ticker, a second venue's RIC — registered with `is_primary=False`?
- [ ] Blank and non-string fields rejected rather than stored? A blank symbol registers
      the key `("", "")`, which then answers every blank lookup an upstream feed emits.

## Conflicts

- [ ] Running with `allow_ambiguous=False` (the default) in anything that feeds routing or
      position aggregation?
- [ ] Every `AmbiguousMappingError` triaged to a cause — recycled symbol (date it), second
      symbol at one vendor (`is_primary=False`), or an upstream merge error (fix the
      source) — and never resolved by overwriting?
- [ ] If `allow_ambiguous=True` was used to load legacy data, `registered_conflicts()` and
      the report's `ambiguous_mappings` / `ambiguous_reverse_mappings` triaged before the
      table is trusted?

## Resolution

- [ ] `as_of` passed for every historical resolution — backtest bars, replayed ticks,
      restated positions — using the observation's own date?
- [ ] Understood that omitting `as_of` means *currently effective*, so a closed window
      returns `None` rather than the previous issuer?
- [ ] Reverse lookup output confirmed byte-for-byte usable against the vendor API or
      router (`AAPL US Equity`, not `AAPL US EQUITY`)?
- [ ] `translate` misses handled as misses — no fallback that routes on the canonical
      symbol?

## Symbology changes

- [ ] Rename modelled as `retire_mapping(..., D)` plus a new entry `effective_from=D`, so
      the windows abut with no gap and no overlap?
- [ ] Return count of `retire_mapping` checked — zero is a failed retirement, not a no-op?
- [ ] Coverage re-run either side of the changeover date, both `FULL_COVERAGE` for the
      affected symbol?
- [ ] Registration driven from a corporate-action or change-notification feed, not from
      manual entry? Nothing here detects a change nobody entered.

## Coverage

- [ ] `expected_canonical` set to the universe actually traded — otherwise the report
      measures the table against itself and cannot show a gap?
- [ ] `expected_vendors` set, and `missing_vendor_coverage` empty for every instrument a
      strategy depends on?
- [ ] Understood that `FULL_COVERAGE` says nothing about whether the mappings are *current*?

## Boundaries

- [ ] Symbol mapping kept separate from identifier validation
      (`isin-cusip-sedol-cross-reference-service`) and from field-level authority
      (`reference-data-golden-source-designation`)?
- [ ] Licensing of held RIC / Bloomberg ticker strings reviewed separately from the code?
