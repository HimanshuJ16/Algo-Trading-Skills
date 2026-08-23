# Standards for Data Retention Policy and Storage Tiering

Two distinct rule families govern this skill: **cloud storage-class mechanics**
(what a transition actually costs and constrains) and **recordkeeping law**
(what may not be deleted, and how fast it must be produced). Confusing them is
the failure mode this skill exists to prevent.

## 1. Recordkeeping — SEC Rule 17a-4 (US broker-dealers)

Retention periods are **record-type specific**. There is no single "6-year
rule", and applying one uniformly is both over-retention for some records and
under-retention for others.

| Provision | Period | Accessibility constraint |
|---|---|---|
| 17 CFR 240.17a-4(a) | Not less than **6 years** (blotters, ledgers and the other §240.17a-3 records it enumerates) | First **two years** in an "easily accessible place" |
| 17 CFR 240.17a-4(b)(1) | Not less than **3 years** (further §240.17a-3 records, including memoranda of brokerage orders) | First **two years** in an "easily accessible place" |
| 17 CFR 240.17a-4(j) | — | Records must be furnished **promptly** to a Commission representative, in a reasonably usable electronic format if requested |

**Electronic recordkeeping format.** Before the 2022 amendments, Rule 17a-4(f)
required electronic records to be preserved exclusively in non-rewriteable,
non-erasable (WORM) format. The SEC adopted amendments on 2022-10-12 that
**retain WORM as an option and add an audit-trail alternative**: a system that
permits recreation of an original record if it is modified or deleted, with a
complete time-stamped audit trail of every creation, modification and deletion,
the actor where applicable, and supporting information. Effective date
2023-01-03; broker-dealer compliance date 2023-05-03.

**Implication for tiering.** The "easily accessible place" requirement is the
binding constraint on archive tier selection for the first two years, and
17a-4(j)'s "furnish promptly" obligation persists for the whole period. A
storage class whose restore takes hours (S3 Glacier Flexible Retrieval, S3
Glacier Deep Archive) cannot serve a record inside the two-year window. Classes
with millisecond retrieval (S3 Standard, S3 Standard-IA, S3 Glacier Instant
Retrieval) can. `DataRetentionPolicyEngine` enforces this as
`min_instant_access_days` (default 730).

**Jurisdiction.** Rule 17a-4 binds SEC-registered broker-dealers. Other regimes
impose their own periods and their own accessibility standards; this skill takes
the applicable period as an *input* (`regulatory_retention_years`) rather than
inferring it. See `record-retention-periods-by-jurisdiction` and
`data-localization-requirements-for-trade-records`.

Sources:
- 17 CFR § 240.17a-4 — https://www.ecfr.gov/current/title-17/part-240/section-240.17a-4
- SEC Release 34-96034, *Electronic Recordkeeping Requirements for Broker-Dealers, Security-Based Swap Dealers, and Major Security-Based Swap Participants* (2022-10-12) — https://www.sec.gov/files/rules/final/2022/34-96034.pdf
- SEC, *Amendments to Electronic Recordkeeping Requirements for Broker-Dealers* — https://www.sec.gov/investment/amendments-electronic-recordkeeping-requirements-broker-dealers

## 2. Storage mechanics — Amazon S3

These constraints are what make a naive age-based lifecycle rule cost more than
it saves. All are from the S3 User Guide (see sources below).

| Constraint | Value | Consequence |
|---|---|---|
| Minimum storage duration — S3 Standard | none | Free to transition out at any time |
| Minimum storage duration — S3 Standard-IA / One Zone-IA | 30 days | Early transition billed for the remaining days |
| Minimum storage duration — S3 Glacier Instant Retrieval | 90 days | A COLD→DEEP ARCHIVE hop before day 90 incurs a pro-rated early-deletion charge |
| Minimum storage duration — S3 Glacier Deep Archive | 180 days | Purging within 180 days of archiving is billed for the full 180 |
| Minimum billable object size — Standard-IA / One Zone-IA / Glacier IR | 128 KB | A 40 KB Parquet part is billed as 128 KB |
| Default lifecycle transition floor | 128 KB | Since September 2024 S3 Lifecycle **will not transition** objects under 128 KB to any class unless an `ObjectSizeGreaterThan`/`ObjectSizeLessThan` filter overrides it |
| Per-object archive metadata | 40 KB | 32 KB billed at the Glacier/Deep Archive rate + 8 KB billed at **S3 Standard** rates, per object, for Glacier Flexible Retrieval and Deep Archive |
| Chained transitions | — | A single lifecycle rule cannot move an object to a second class before the first class's minimum duration elapses (e.g. GIR at day 4 then Deep Archive at day 20 is invalid; the second must be ≥ day 94) |
| Retrieval latency | ms / minutes–hours / hours | Standard, Standard-IA and Glacier Instant Retrieval are millisecond; Glacier Flexible Retrieval and Deep Archive require a restore job first |
| Deep Archive directionality | one-way | Lifecycle cannot move an object out of Deep Archive; exiting requires restore-then-copy |

The 8 KB-at-Standard-rates metadata slice is the trap for tick data: a fleet of
10 million small objects carries roughly 76 GB of Standard-rate metadata
(~$1.75/month) regardless of how cheap the archive tier is — which can exceed
the archive storage charge itself. Compact before archiving.

Sources:
- *Transitioning objects using S3 Lifecycle* — https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html
- *Understanding and managing Amazon S3 storage classes* (storage-class comparison table) — https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html
- *Amazon S3 pricing* — https://aws.amazon.com/s3/pricing/

## 3. Pricing assumptions in this skill

`TIER_PRICING_USD_PER_GB` holds **illustrative us-east-1 list prices** for
worked examples, not a live price feed:

| Tier constant | Modelled as | Price/GB-month |
|---|---|---|
| `HOT_NVME` | self-managed NVMe / block store | $0.20 |
| `WARM_PARQUET_S3` | S3 Standard, first 50 TB band | $0.023 |
| `COLD_GLACIER_INSTANT` | S3 Glacier Instant Retrieval | $0.004 |
| `DEEP_ARCHIVE` | S3 Glacier Deep Archive | $0.00099 |

Known limitations of the model, all deliberate:

- **S3 Standard list pricing is volume-tiered** (the first 50 TB/month band is
  used here as a flat rate), so a 100 TB single-dataset example overstates the
  S3 Standard bill and therefore the savings.
- Per-GB **retrieval** fees, **data transfer** and request charges other than an
  optionally-supplied transition price are **not** modelled.
- The `HOT_NVME` figure is a placeholder for self-managed storage and depends
  entirely on the hardware or instance family in use.

Pass a `pricing_map` reflecting your own region and negotiated rates before
quoting any savings figure to a stakeholder.
