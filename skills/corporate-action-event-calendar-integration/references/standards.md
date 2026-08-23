# Standards for Corporate Action Calendar Integration

| Metric | Engineering Standard |
|---|---|
| Complete 4-Date Schema | All corporate action events MUST record declaration, ex-date, record date, and payment date, and MUST satisfy `declaration <= ex <= record <= payment` (`ex == record` is valid under T+1). |
| Multi-Vendor Discrepancy Resolution | Reconciliation MUST run in both directions. Ex-date, record-date, payment-date, or value mismatches — and events present in only one feed — MUST trigger automated compliance review before the ex-date. |
| Settlement Window Alignment | Record-date logic MUST account for settlement cycles: under T+1 the ex-date is generally the same business day as the record date; under T+2 it is one business day earlier. |
| Idempotent Ingestion | Event registration MUST deduplicate on `event_id` so vendor re-broadcasts are never double-counted. |
| Entitlement Basis | Dividend entitlement belongs to holders at the close preceding the ex-date; buying on or after the ex-date creates no entitlement. |

## External References (verified 2026-08)

| Source | Reference | Relevance | Jurisdiction |
|---|---|---|---|
| SEC | Rule 15c6-1(a), as amended — standard settlement cycle shortened from T+2 to T+1; compliance date 2024-05-28. [Small Entity Compliance Guide](https://www.sec.gov/investment/settlement-cycle-small-entity-compliance-guide-15c6-1-15c6-2-204-2) | Governs settlement timing that determines ex-date/record-date spacing | US |
| NYSE / FINRA | NYSE Section 204.12 amendments and [FINRA SR-FINRA-2023-017](https://www.finra.org/sites/default/files/2023-11/SR-FINRA-2023-017.pdf) (effective 2024-05-28): ex-dividend basis generally falls on the record date under T+1. See also [SEC filing 34-99871](https://www.sec.gov/files/rules/sro/nyse/2024/34-99871.pdf) | Exchange-level ex-date convention | US |
| ISO 15022 | [MT 564 Corporate Action Notification](http://www.iso20022.org/15022/uhb/finmt564.htm) and MT 566 Corporate Action Confirmation | Canonical message standards for vendor/issuer corporate action distribution and payment confirmation | Global |

Note: ex-date conventions above are US-specific. Other jurisdictions still operating T+2 markets (e.g. much of EMEA/APAC pending their own T+1 transitions) generally keep the ex-date one business day before the record date — parameterize validation per market rather than hard-coding one convention.
