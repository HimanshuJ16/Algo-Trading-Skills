---
name: reference-data-symbol-mapping-across-vendors
description: >-
  Use when data from several vendors must join on the same instrument across tickers,
  RICs, ISINs, CUSIPs, SEDOLs and FIGIs, resolving each to one canonical internal symbol
  point-in-time and back again.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: symbol-mapping, reference-data, cross-vendor, point-in-time, isin, cusip, sedol, bloomberg, reuters-ric
  brokers_frameworks: "Bloomberg Ticker Symbology; Refinitiv Identification Code (RIC); ISO 6166 ISIN; CUSIP Global Services; SEDOL Masterfile; OpenFIGI; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when data from more than one vendor has to join on the same instrument. Apple Inc. is `AAPL US Equity` to Bloomberg (the **composite** exchange code) and also `AAPL UW Equity` (the **primary** exchange code, Nasdaq); `AAPL.O` as a Refinitiv RIC; `US0378331005` as an ISIN; `BBG000B9XRY4` as a FIGI. Every one of those is correct, none of them are interchangeable, and a join that assumes otherwise silently aggregates the wrong positions.

The engine holds one canonical internal symbol per instrument, maps each vendor's symbol to it over an **effective-dated window**, and resolves in both directions at a caller-supplied point in time.

| Property | Why the naive table gets it wrong |
|---|---|
| A vendor symbol is unique only **within a window** | Exchange tickers are recycled. NYSE `S` was Sprint until the NYSE removed the class from listing on 13 April 2020; SentinelOne listed under `S` on 30 June 2021. A table keyed on `("NYSE", "S")` alone resolves a 2019 tick to SentinelOne. |
| One canonical symbol can have **several symbols at one vendor** | `AAPL US Equity` (composite) and `AAPL UW Equity` (Nasdaq primary) are both Bloomberg's. Last-write-wins hands a router whichever was loaded second. |
| A conflicting registration is a **data defect** | Pointing a live vendor key at a second canonical symbol repoints every join already keyed on it. It has to raise, not warn. |

## When NOT to Use

- **To validate an identifier.** Nothing here checks a check digit; `US0378331005` and `US0378331009` are both accepted as opaque vendor strings. Use `isin-cusip-sedol-cross-reference-service` at the ingest boundary, *before* registering a mapping.
- **To decide which vendor is right about a field.** Symbol mapping answers "which rows are the same instrument", not "whose tick size is correct". That is `reference-data-golden-source-designation`.
- **As a corporate-action feed.** The table is only as current as what was registered. A rename nobody entered is a mapping that stays wrong and never warns. Drive registration from `corporate-action-event-calendar-integration` and `reference-data-change-notification-pipeline`.
- **For intraday symbology changes.** Windows are date-granular and half-open. They model listing-lifecycle events, which happen between sessions.
- **As a substitute for a licensing review.** RIC and Bloomberg ticker strings are licensed vendor symbology. Holding them in an internal table is a contractual question — see `data-vendor-contractual-usage-restriction-tracking`.

## Prerequisites

- A canonical internal symbol per instrument. Prefer one that does **not** move on a rename: `FB` became `META` on 9 June 2022 while the listing and the CUSIP were unchanged. Key on the FIGI or an internal surrogate, carry the ticker as an attribute.
- `VendorSymbolEntry` rows: `canonical_symbol`, `vendor_name`, `vendor_symbol` (verbatim, as the vendor writes it), `identifier_type`, and optionally `effective_from` / `effective_to` / `is_primary`.
- A `SymbolMappingConfig` decision: `case_sensitive` (default False) and `allow_ambiguous` (default False — conflicts raise).
- For historical resolution, the observation's own date, to pass as `as_of`.

## Workflow

1. **Register mappings, verbatim and dated**:
   - Store the vendor's spelling exactly. Normalisation (trim, collapse internal whitespace runs, upper-case) applies to lookup **keys** only, because the value is what gets handed back to a vendor API or an order router — and `AAPL US EQUITY` is not a Bloomberg ticker.
   - Date anything whose window you know. Undated means "currently effective, open-ended in the past", which is right for a symbol that has never moved and wrong for every one that has.
   - Exactly one entry per (canonical, vendor, window) carries `is_primary=True` — the symbol a reverse lookup returns. Register the alternates (`AAPL UW Equity`) with `is_primary=False`: they still resolve *inbound*, they are just not the routing answer.
2. **Let a conflict fail the load**:
   - A vendor key already resolving to a different canonical symbol over an overlapping window raises `AmbiguousMappingError`. A second `is_primary` symbol for one (canonical, vendor) raises too. Re-registering an identical mapping is idempotent, so re-running an ingest is safe.
   - Only set `allow_ambiguous=True` to bulk-load known-dirty legacy data you intend to audit rather than repair. The **first** registration still wins every lookup, the conflict is logged at ERROR each time it is *read*, and it appears in `registered_conflicts()` and the coverage report. It is never resolved silently.
3. **Resolve at a point in time**:
   - `forward_lookup(vendor, symbol, as_of=...)` → canonical. `reverse_lookup(canonical, vendor, as_of=...)` → that vendor's primary symbol. `translate(source_vendor, symbol, target_vendor, as_of=...)` chains both and returns `None` if either leg misses — never the canonical symbol as a consolation, which a caller would route on.
   - Omitting `as_of` means **currently effective**, never "whatever was registered last". A window that has closed does not answer: a miss you can alert on beats a confident answer naming the previous issuer.
4. **Handle the rename as two abutting windows**:
   - `retire_mapping(vendor, old_symbol, changeover_date)` closes the open window; register the new symbol with `effective_from=changeover_date`. Half-open windows mean one date, no gap and no overlap. Check the returned count — zero means nothing was retired, which is a failed retirement, not a no-op.
5. **Report coverage against the universe you actually trade**:
   - `get_coverage_report(expected_canonical=..., expected_vendors=..., as_of=...)`. Without `expected_canonical` the report measures the table against itself and cannot show a gap. `expected_vendors` surfaces per-vendor holes (`AAPL@FACTSET`) — the question that decides whether a strategy can run.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Returning a normalised symbol from a reverse lookup.** Upper-casing the *key* is correct; upper-casing the *value* produces `AAPL US EQUITY`, which no Bloomberg interface accepts. Normalise keys, store and return values verbatim.
- **Treating `(vendor, symbol)` as a permanent identity.** NYSE `S` meant Sprint through 13 April 2020 and SentinelOne from 30 June 2021, with fourteen months in between when it meant nothing. Resolving a historical tick without `as_of` attributes Sprint's prints to SentinelOne — and the backtest will not crash, it will just be wrong.
- **Assuming one symbol per vendor per instrument.** Bloomberg's composite and primary-exchange tickers are both live and both correct. Silently keeping the last one registered routes to the wrong venue's ticker roughly half the time.
- **Resolving a conflict by overwriting.** The second registration is evidence of an upstream merge error. Overwriting repoints every join already keyed on that symbol and destroys the evidence. Raise, or record the conflict and keep the first.
- **Re-keying the canonical symbol on the ticker.** A rename then looks like a delisting plus a new instrument, and position history splits in two. `FB` → `META` moved the ticker; the CUSIP and the listing did not move.
- **Registering a blank symbol from a ragged CSV row.** It creates the key `("", "")`, which then answers every blank-string lookup an upstream feed produces. Blank fields are rejected outright.
- **Trusting `identifier_type` as a validation.** It is a label the feed supplied. `RIC` on a column full of ISINs is accepted; only a check-digit validator catches it.
- **Assuming a RIC root identifies a listing.** A RIC is root + `.` + a one- or two-character exchange code. `AAPL.O` is Nasdaq; the root `AAPL` on its own is not a mapping key.

## Verification

- Instantiate `SymbolMappingEngine()` and register Apple at Bloomberg (`AAPL US Equity`, primary), Bloomberg (`AAPL UW Equity`, `is_primary=False`), Refinitiv (`AAPL.O`) and an ISIN source (`US0378331005`). Forward lookup `AAPL.O` at Refinitiv $\implies$ `AAPL`. Reverse lookup `AAPL` at Bloomberg $\implies$ **`AAPL US Equity`**, byte-for-byte as registered. `reverse_lookup_all` $\implies$ both Bloomberg tickers.
- Verify conflict rejection: register `SPRINT` → NYSE `S`, then `SENTINELONE` → NYSE `S` undated $\implies$ `AmbiguousMappingError`, and `forward_lookup("NYSE", "S")` still returns `SPRINT`. Register `AAPL UW Equity` as a second *primary* Bloomberg symbol $\implies$ `AmbiguousMappingError`.
- Verify point-in-time resolution: register `SPRINT_CORP` → NYSE `S` with `effective_to=2020-04-13` and `SENTINELONE` → NYSE `S` with `effective_from=2021-06-30` $\implies$ no conflict; `as_of=2019-06-01` returns `SPRINT_CORP`, `as_of=2020-06-01` returns `None`, `as_of=2021-06-30` returns `SENTINELONE`, and omitting `as_of` returns `SENTINELONE`. Confirm the window is half-open: `2020-04-13` misses, `2020-04-12` hits.
- Verify the rename path: register `META_PLATFORMS` → Nasdaq `FB`, `retire_mapping("Nasdaq", "FB", 2022-06-09)` $\implies$ returns 1, then register `META` from `2022-06-09` $\implies$ no conflict; `FB` resolves at `2022-06-08` and misses today.
- Verify validation: a blank `vendor_symbol`, a non-string symbol, and `effective_from >= effective_to` $\implies$ `ValueError` in every case.
- Verify coverage: `get_coverage_report(expected_canonical=["AAPL", "MSFT"], expected_vendors=["Bloomberg", "FactSet"])` $\implies$ `PARTIAL_COVERAGE`, `unmapped_canonical == ["MSFT"]`, `missing_vendor_coverage` naming `AAPL@FACTSET`.
- Run `python -m unittest discover -s skills/reference-data-symbol-mapping-across-vendors/scripts`.

## Related Skills

- `isin-cusip-sedol-cross-reference-service`
- `reference-data-golden-source-designation`
- `reference-data-change-notification-pipeline`
- `corporate-action-event-calendar-integration`
- `instrument-universe-change-detection-and-alerting`
- `multi-exchange-feed-normalization`
- `data-vendor-contractual-usage-restriction-tracking`
