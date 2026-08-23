# Standards for Corporate Action Calendar Integration

| Metric | Engineering Standard |
|---|---|
| Complete 4-Date Schema | All corporate action events MUST record declaration, ex-date, record date, and payment date, and MUST declare an ex-date convention. |
| Ex-Date Placement | Distributions worth less than 25% of the security MUST satisfy `declaration <= ex <= record <= payment` (`ex == record` is valid under T+1). Distributions of 25% or more MUST satisfy `declaration <= record <= payment <= ex` (FINRA Rule 11140(b)(2)). The convention MUST be supplied by the feed, never inferred from event type or value. |
| Multi-Vendor Discrepancy Resolution | Reconciliation MUST run in both directions. Symbol, event-type, ex-date-convention, ex-date, record-date, payment-date, or value mismatches — and events present in only one feed — MUST trigger automated compliance review before the ex-date. |
| Settlement Window Alignment | Record-date logic MUST account for settlement cycles: under T+1 the ex-date is generally the same business day as the record date; under T+2 it is one business day earlier. |
| Idempotent Ingestion | Event registration MUST deduplicate on `event_id` so vendor re-broadcasts are never double-counted. A re-broadcast whose payload differs materially MUST be surfaced as an amendment (ISO 15022 MT 564 function `REPL`) rather than silently discarded or silently applied. |
| Entitlement Basis | Dividend entitlement belongs to holders at the close preceding the ex-date; buying on or after the ex-date creates no entitlement. |

## External References (verified 2026-08)

| Source | Reference | Relevance | Jurisdiction |
|---|---|---|---|
| SEC | Rule 15c6-1(a), as amended — standard settlement cycle shortened from T+2 to T+1; compliance date 2024-05-28. [Small Entity Compliance Guide](https://www.sec.gov/investment/settlement-cycle-small-entity-compliance-guide-15c6-1-15c6-2-204-2) | Governs settlement timing that determines ex-date/record-date spacing | US |
| NYSE / FINRA | NYSE Section 204.12 amendments and [FINRA SR-FINRA-2023-017](https://www.finra.org/sites/default/files/2023-11/SR-FINRA-2023-017.pdf) (effective 2024-05-28): ex-dividend basis generally falls on the record date under T+1. See also [SEC filing 34-99871](https://www.sec.gov/files/rules/sro/nyse/2024/34-99871.pdf) | Exchange-level ex-date convention | US |
| FINRA | [Rule 11140(b)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/11140), as amended by SR-FINRA-2023-017 (operative 2024-05-28): (b)(1) distributions under 25% of the security's value go ex on the record date; **(b)(2) distributions of 25% or more go ex on the first business day following the payable date**. See also [Regulatory Notice 24-04](https://www.finra.org/rules-guidance/notices/24-04) | Determines whether the ex-date precedes the record date or follows the payable date | US |
| NVIDIA (worked example) | [Form 8-K, 2024-06-07](https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/nvda-20240607.htm) — 10-for-1 split: record 2024-06-06, payable 2024-06-07, split-adjusted trading from 2024-06-10 | Confirms the (b)(2) ordering `record < payment < ex` in practice | US |
| ISO 15022 | [MT 564 Corporate Action Notification](http://www.iso20022.org/15022/uhb/finmt564.htm) (message function `NEWM` / `REPL` replacement / `CANC` cancellation, field 23G) and MT 566 Corporate Action Confirmation | Canonical message standards for vendor/issuer corporate action distribution; `REPL` is how amendments arrive under an existing identifier | Global |

Note: the 25% threshold and ex-date conventions above are US-specific. Other jurisdictions still operating T+2 markets (e.g. much of EMEA/APAC pending their own T+1 transitions) generally keep the ex-date one business day before the record date — parameterize validation per market rather than hard-coding one convention.
