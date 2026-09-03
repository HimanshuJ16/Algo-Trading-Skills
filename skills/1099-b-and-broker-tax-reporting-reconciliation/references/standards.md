# Standards for 1099-B and Broker Tax Reporting Reconciliation

Jurisdiction: **United States federal income tax.** Every rule below is US
federal and none of it carries to another regime.

This reference defines the regulatory and reporting concepts the reconciliation
engine enforces. Each row cites the authority it rests on; where this skill has
an operating convention rather than a legal requirement, it says so explicitly.

| Standard / Term | Description |
|---|---|
| **IRS Form 1099-B** | *Proceeds from Broker and Barter Exchange Transactions.* The official broker-to-IRS document issued by the clearing broker for each tax year. Boxes 1d (proceeds), 1e (basis), 1f (accrued market discount), 1g (wash-sale loss disallowed) are the in-scope fields for covered securities. Source: IRS Form 1099-B instructions. |
| **IRS Form 8949** | *Sales and Other Dispositions of Capital Assets.* Taxpayer filing form. Column (d) proceeds, column (e) basis, **column (f) adjustment code**, column (g) adjustment amount, column (h) gain/loss. Part I is short-term (boxes **A**, **B**, **C**), Part II long-term (boxes **D**, **E**, **F**); the further boxes **G/H/I** and **J/K/L** are the digital-asset counterparts reported on Form 1099-DA and are out of scope here. Within the 1099-B boxes the `covered` flag selects **A/D** (basis reported to the IRS) versus **B/E** (not reported); **C/F** is for dispositions with no Form 1099-B at all. Source: [Instructions for Form 8949](https://www.irs.gov/instructions/i8949). |
| **Trade-Date Accounting** | A security's holding period runs from the day after the trade date of the purchase to the trade date of the sale, not the settlement date ([Pub. 550](https://www.irs.gov/publications/p550), *Holding Period → Securities traded on an established market*). The broker reports the same basis: box 1c is "the trade date of the sale or exchange" ([Instructions for Form 1099-B](https://www.irs.gov/instructions/i1099b)). So a Dec 30/31 sale settling in January under SEC Rule 15c6-1 T+1 (effective 2024-05-28) still belongs to the **current** tax year *and* the current year's 1099-B — settlement drift is not an explanation for a missing broker row. |
| **Section 1091 — Wash Sale** | A loss on a sale of a security is disallowed if a "substantially identical" security was purchased within **±30 calendar days** (61-day window total) of the loss-sale date. The disallowed amount **is added to the basis of the replacement share** (not lost). Reportable via Form 8949 column (f) code `W`. Brokers typically only flag same-account, same-CUSIP transactions; cross-account wash sales are the taxpayer's responsibility. |
| **Section 1256 Contracts** | Special 60/40 character treatment (60% long-term, 40% short-term) for regulated futures contracts (CME, ICE), broad-based index options (SPX, NDX), and certain foreign-currency contracts. Reported on Form 1099-B **aggregated** in boxes 8–11 (mark-to-market) and on Form 6781. **Out of scope** for this engine — the per-lot model does not linearly apply. |
| **Covered Security** | A lot acquired **for cash after** the effective date for its instrument type. Per the [Instructions for Form 1099-B](https://www.irs.gov/instructions/i1099b): stock **after 2010**; stock for which the average basis method is available **after 2011**; less complex debt instruments, options and securities futures contracts **after 2013**; the more complex debt instruments (variable-rate, inflation-indexed, contingent payment, convertible) **after 2015**. Note each is "after year X", i.e. acquired on or after 1 January of X+1. Digital assets are reported on Form 1099-DA, not 1099-B: gross proceeds for sales effected on or after 2025-01-01, and basis for covered digital assets for sales effected after 2025. Broker reports basis to the IRS in **box 12**. Routes to Form 8949 box A (short-term) or D (long-term). |
| **Noncovered Security** | **Box 5** on Form 1099-B is checked; basis was *not* reported to the IRS. Routes to Form 8949 box B (short-term) or E (long-term), where the taxpayer enters the correct basis directly in column (e) and `-0-` in column (g). |
| **Adjustment Codes (Form 8949 column f)** | Letter codes for column (g) adjustments. Subset relevant to reconciliation: `B` (incorrect basis), `W` (wash sale disallowed), `T` (incorrect gain/loss type), `N` (nominee), `M` (multiple transactions on one row), `O` (other). Full list at IRS Form 8949 instructions. |
| **Specific Identification vs FIFO vs Average Cost** | Lot-selection method. Default is FIFO when the taxpayer has not made an election; specific identification requires contemporaneous documentation. Reconciliation requires the internal ledger track the *same* method the taxpayer is filing under, otherwise the lot-mapping breaks. |
| **Constructive Sale (§1259)** | Generally out of scope for ordinary equity trading; relevant to forward/futures and short-sales against the box. Must be combined with §1091 logic if flagged. |
| **§1091(d) Carryover Basis** | The wash-sale basis add-on for replacement shares: the disallowed loss is added to the basis of the replacement lot rather than lost ([26 U.S.C. §1091(d)](https://www.law.cornell.edu/uscode/text/26/1091)). The internal ledger must propagate carryover basis forward before reconciliation, or every replacement lot will show a `BASIS_OUTSIDE_TOLERANCE` against the broker. (This is **not** §263, which governs capitalization of capital expenditures and has no bearing on wash sales.) |
| **Mark-to-Market Election (§475)** | Speculative trader election; marks all positions to year-end FMV. Drastically changes lot reporting (Form 4797 instead of 8949). Out of scope here. |
| **Short Sales** | A broker does not report a short sale entered into after 2010 "until the year a customer delivers a security to satisfy the short sale obligation" ([Instructions for Form 1099-B](https://www.irs.gov/instructions/i1099b)). This is the one routine case where a realized internal lot legitimately does not appear on the same year's 1099-B. The engine does **not** model short sales at all: its `sold_date < acquired_date ⇒ reject` guard is a data-quality check for reversed dates, not short-sale support, and it is unrelated to SEC Regulation SHO (a pre-trade locate requirement for short *orders*, with no tax-reporting effect). Net short positions must be resolved before ingestion. |

## Discrepancy reason taxonomy

`DiscrepancyReason` has seven members, six of which the engine emits. The
seventh, `DUPLICATE_LOT_ID`, is reserved: duplicate ids are rejected at
ingestion with a `ValueError` rather than surfaced as a discrepancy.

| Engine `DiscrepancyReason` | IRS remediation | Recommended 8949 column (g) code |
|----------------------------|-----------------|----------------------------------|
| `MISSING_IN_BROKER` | Confirm trade settles in the *next* tax year; document. | None (excluded from current-year 8949) |
| `MISSING_IN_INTERNAL` | Investigate import miss; do not file 8949 until resolved. | Reject, fix root cause, rerun. |
| `WASH_SALE_FLAG_MISMATCH` | Compare against internal trade tickets; broker typically authoritative for same-account, same-CUSIP. | `W` if internal flagged and broker missed. |
| `WASH_SALE_AMOUNT_MISMATCH` | Compare exact disallowed $; either side may be authoritative. | `W` with column (g) = internal's value or explanatory statement. |
| `BASIS_OUTSIDE_TOLERANCE` | Substantiate which side is correct first. Covered: report the broker's box 1e in column (e) and adjust in column (g). Noncovered: report the correct basis in column (e) and `-0-` in column (g). | `B` (covered only) with column (g) = **broker − internal**, i.e. `-basis_delta`; no code for the noncovered case. |
| `PROCEEDS_OUTSIDE_TOLERANCE` | Verify against trade ticket / confirmation report. | `O` if non-recoverable. |

## Numerical conventions

- All monetary fields are represented as `decimal.Decimal`, quantized to **cents** (2 fractional digits) at result emission.
- Quantity is represented as `decimal.Decimal` with a working precision of **8 fractional digits** (covers fractional shares to 8 dp — most equity plans).
- Tolerance is dual: **absolute cents** (default `$0.05`) *and* **relative basis-percent** (default `0.0001` = 1 bp). A difference passes if it satisfies *either* bound.
- Dates are `datetime.date`, so they carry no time or zone and DST is not a factor. The real convention risk is upstream: both sides must express the same *calendar* and the same *event*. Normalize to the broker's local trade date before ingestion — an execution timestamp captured in UTC can land on the previous or next calendar day relative to the US market session that the 1099-B reports.
- Lot IDs must be unique within each side (internal ledger, broker 1099-B). Duplicate IDs raise at ingestion time, not silently.

## Security / PII guidance

Form 1099-B data is **IRS-sensitive PII** (full name, address, partial TIN, dollar positions). When operating on 1099-B data:

- **Encryption at rest** — AES-256 for ledger persistence; KMS-managed keys.
- **Encryption in flight** — TLS 1.2+ (preferably 1.3); no plain-HTTP broker portals.
- **Access logging** — every read of the reconciliation artifact must be audit-logged (`audit-logging-for-configuration-changes` style pattern).
- **Retention** — the IRS period-of-limitations baseline is **3 years**, extending to **6 years** where more than 25% of gross income is omitted, **7 years** only for a claim of loss from worthless securities or a bad-debt deduction, and **indefinite** where no return or a fraudulent return was filed ([How long should I keep records?](https://www.irs.gov/businesses/small-businesses-self-employed/how-long-should-i-keep-records); the underlying assessment periods are [26 U.S.C. §6501](https://www.law.cornell.edu/uscode/text/26/6501)). Separately, cost-basis records must survive as long as they are needed to compute basis, which for a long-held lot outlives all of the above. A 7-year floor is a common firm policy, not a universal IRS requirement. See `record-keeping-requirements-for-tax-audit-defense` and `record-retention-periods-by-jurisdiction`.
- **No external LLM inference** on raw 1099-B contents; redact lot IDs and identifiers before any LLM-assisted review.
