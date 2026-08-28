# Workflows for Reference Data Symbol Mapping Across Vendors

## 1. Build the mapping table

1. Validate identifiers at the ingest boundary first
   (`isin-cusip-sedol-cross-reference-service`). This engine stores opaque strings; a
   corrupted ISIN registers just as happily as a good one.
2. Choose a canonical symbol that does not move on a rename — a FIGI or an internal
   surrogate. Not the ticker.
3. Build one `VendorSymbolEntry` per (vendor, symbol, window), storing `vendor_symbol`
   **exactly as the vendor writes it**.
4. Date every window you know. `effective_to` is exclusive.
5. Mark exactly one entry per (canonical, vendor, window) `is_primary=True`. Alternates
   — Bloomberg's primary-exchange ticker next to its composite, a secondary venue's RIC —
   register with `is_primary=False` and still resolve inbound.
6. Register. `register_mapping` is idempotent for an identical row, so re-running the
   ingest is safe.

## 2. Triage a conflict — do not resolve it in code

`AmbiguousMappingError` means one lookup key would resolve two ways over one window. It is
upstream evidence, and there are only three real causes:

| Cause | Fix |
|---|---|
| The symbol was recycled or renamed | Date both entries. The windows abut and the conflict disappears. |
| Two vendor symbols for one instrument at one vendor | Register the alternate with `is_primary=False`. |
| Two rows genuinely claim one symbol | An upstream merge error. Fix the source. Do not overwrite. |

`allow_ambiguous=True` exists only to bulk-load dirty legacy data for audit: the first
registration keeps winning, every read logs at ERROR, and the conflict is listed by
`registered_conflicts()` and in `get_coverage_report().ambiguous_mappings` /
`.ambiguous_reverse_mappings`. It is not a fix.

## 3. Resolve

| Direction | Call | Returns |
|---|---|---|
| Vendor → canonical | `forward_lookup(vendor, symbol, as_of=…)` | canonical symbol, as registered |
| Canonical → vendor | `reverse_lookup(canonical, vendor, as_of=…)` | that vendor's **primary** symbol, verbatim |
| Canonical → all of one vendor's symbols | `reverse_lookup_all(canonical, vendor, as_of=…)` | entries, so `is_primary` and `identifier_type` stay visible |
| Vendor → vendor | `translate(source, symbol, target, as_of=…)` | target symbol, or `None` if either leg misses |

Rules that decide correctness:

- Pass the **observation's own date** as `as_of` for anything historical — a backtest bar,
  a replayed tick, a restated position. A `datetime` is accepted and truncated to its date.
- Omitting `as_of` means *currently effective*. A closed window never answers. A miss you
  can alert on beats a confident answer naming the previous issuer.
- `translate` returns `None`, not the canonical symbol, when the target leg misses. A
  caller expecting a vendor symbol would route on whatever it got back.

## 4. Apply a symbology change

On a rename, relisting or ticker transfer, with `D` the first date the new symbol is live:

1. `retire_mapping(vendor, old_symbol, D)` — closes the open window. **Check the return
   count.** Zero means nothing matched, which is a failed retirement, not a no-op.
2. `register_mapping(VendorSymbolEntry(..., new_symbol, effective_from=D))`.
3. Re-run the coverage report at `as_of=D` and at `as_of=D - 1 day`; both must be
   `FULL_COVERAGE` for the affected canonical symbol. A gap between the windows means the
   dates are wrong.

The canonical symbol never changes. That is the point of having one.

## 5. Report coverage

```
get_coverage_report(expected_canonical=…, expected_vendors=…, as_of=…)
```

- Without `expected_canonical`, the report measures the table against itself and cannot
  show a gap. Pass the universe you actually trade.
- `expected_vendors` produces `missing_vendor_coverage` (`AAPL@FACTSET`) — the question
  that decides whether a strategy can run at all.
- Counts are scoped to `as_of`: a retired ticker is neither a gap nor a conflict.
- `FULL_COVERAGE` means no gaps *in what was registered*. It is not a statement that the
  mappings are current — only a change feed can tell you that.
