# Pre-Flight Checklist

## Entitlement inventory

- [ ] Is there one `VenueEntitlement` per venue **as licensed** — CME, CBOT, NYMEX and COMEX as four records, not one "CME"?
- [ ] Is `max_data_level` traced to the venue product actually on the Order Form, with the product-to-`L1`/`L2`/`L3` mapping written down?
- [ ] Are `non_display_categories` taken from the executed declaration, not inferred from the product name — and left empty where the entitlement is display-only?
- [ ] Is `license_expiry_date` populated from the Order Form, rather than left as a placeholder far-future date?
- [ ] Where expiry is deliberately untracked (`None`), is the logged warning going somewhere a human reads?

## Subscriber classification

- [ ] Is every organisation-held account classified `PROFESSIONAL`, regardless of who uses it?
- [ ] Has the distributor **verified** each `NON_PROFESSIONAL` declaration rather than accepted the subscriber's assertion?
- [ ] Is `classification_attested_on` set, and is re-verification scheduled at least semi-annually?
- [ ] Are automated consumers running under a Professional-tier entitlement?

## Request gating

- [ ] Does every consumer of venue data pass through the gate *before* the stream is opened?
- [ ] Does the request carry a `non_display_category` for every `NON_DISPLAY_ALGO` request, sourced from the consumer's actual activity rather than a default?
- [ ] Are risk engines, auto-hedgers, smart order routers and pre-trade checks classified as non-display — not just alpha strategies?
- [ ] Is `as_of_date` passed explicitly in batch, backfill, replay and test contexts so decisions are reproducible?
- [ ] Does the caller treat any unrecognised `status` value as a denial rather than falling through to approval?

## Audit evidence

- [ ] Is every `EntitlementAuditReport` — denials included — persisted durably as it is returned?
- [ ] Does retention cover the audit look-back period (three years under the Nasdaq Global Data Agreement)?
- [ ] Are reportable units derived from the infrastructure inventory rather than from this engine, which counts nothing?
- [ ] Are CME Applications declared and reported in the month they are added or removed?
- [ ] Is the encoded inventory reconciled against the executed Order Forms on a schedule, so drift surfaces before an audit does?
