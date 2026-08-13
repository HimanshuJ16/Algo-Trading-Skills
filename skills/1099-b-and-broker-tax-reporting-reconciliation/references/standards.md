# Standards for 1099-B and Broker Tax Reporting Reconciliation

This reference defines the regulatory and reporting concepts the reconciliation
engine enforces. Cross-references point to authoritative IRS / SEC sources.

| Standard / Term | Description |
|---|---|
| **IRS Form 1099-B** | *Proceeds from Broker and Barter Exchange Transactions.* The official broker-to-IRS document issued by the clearing broker for each tax year. Boxes 1d (proceeds), 1e (basis), 1f (accrued market discount), 1g (wash-sale loss disallowed) are the in-scope fields for covered securities. Source: IRS Form 1099-B instructions. |
| **IRS Form 8949** | *Sales and Other Dispositions of Capital Assets.* Taxpayer filing form. Column (d) proceeds, column (e) basis, **column (f) adjustment code**, column (g) adjustment amount, column (h) gain/loss. The 8949 box routing (A/B/C/D/E vs G/H/I/J/K/L) is determined by the `covered` flag on each tax lot. |
| **Trade-Date Accounting** | IRC requires trades to be recognized for tax on execution date, not settlement. Combined with SEC Rule 15c6-1 (T+1 settlement since May 28, 2024), this means a Dec 30/31 trade settles in the next calendar year but accrues to the current tax year. Always reconcile against trade date, not settle date. |
| **Section 1091 — Wash Sale** | A loss on a sale of a security is disallowed if a "substantially identical" security was purchased within **±30 calendar days** (61-day window total) of the loss-sale date. The disallowed amount **is added to the basis of the replacement share** (not lost). Reportable via Form 8949 column (f) code `W`. Brokers typically only flag same-account, same-CUSIP transactions; cross-account wash sales are the taxpayer's responsibility. |
| **Section 1256 Contracts** | Special 60/40 character treatment (60% long-term, 40% short-term) for regulated futures contracts (CME, ICE), broad-based index options (SPX, NDX), and certain foreign-currency contracts. Reported on Form 1099-B **aggregated** in boxes 8–11 (mark-to-market) and on Form 6781. **Out of scope** for this engine — the per-lot model does not linearly apply. |
| **Covered Security** | Tax lot acquired after the relevant effective date (2010-01-01 for equities, 2011 for mutual funds, 2013 for options/warrants/debt, **2025-12-31** for digital assets acq'd in 2026 or later). Broker reports basis to IRS in box 12. Use Form 8949 box A/B (short-term) or D/E (long-term). |
| **Noncovered Security** | Box 5 on Form 1099-B is checked; basis was *not* reported to IRS. Taxpayer must report correct basis on Form 8949 column (e) directly. |
| **Adjustment Codes (Form 8949 column f)** | Letter codes for column (g) adjustments. Subset relevant to reconciliation: `B` (incorrect basis), `W` (wash sale disallowed), `T` (incorrect gain/loss type), `N` (nominee), `M` (multiple transactions on one row), `O` (other). Full list at IRS Form 8949 instructions. |
| **Specific Identification vs FIFO vs Average Cost** | Lot-selection method. Default is FIFO when the taxpayer has not made an election; specific identification requires contemporaneous documentation. Reconciliation requires the internal ledger track the *same* method the taxpayer is filing under, otherwise the lot-mapping breaks. |
| **Constructive Sale (§1259)** | Generally out of scope for ordinary equity trading; relevant to forward/futures and short-sales against the box. Must be combined with §1091 logic if flagged. |
| **§263 Carryover Basis** | Wash-sale add-on for replacement shares; replacement's basis = original cost + disallowed amount. Internal ledger must propagate carryover basis forward or post-process this in reconciliation. |
| **Mark-to-Market Election (§475)** | Speculative trader election; marks all positions to year-end FMV. Drastically changes lot reporting (Form 4797 instead of 8949). Out of scope here. |
| **Reg-SHO Locate Rule** | Short-sale rule requiring locate before short order. Tax-side impact: short-sale closing must be evaluated against the original short lot, not the buy lot. Already enforced by the engine through the "sold_date < acquired_date ⇒ reject" guard. |

## Discrepancy reason taxonomy

The engine emits six reasons that map onto IRS reconciliation logic:

| Engine `DiscrepancyReason` | IRS remediation | Recommended 8949 column (g) code |
|----------------------------|-----------------|----------------------------------|
| `MISSING_IN_BROKER` | Confirm trade settles in the *next* tax year; document. | None (excluded from current-year 8949) |
| `MISSING_IN_INTERNAL` | Investigate import miss; do not file 8949 until resolved. | Reject, fix root cause, rerun. |
| `WASH_SALE_FLAG_MISMATCH` | Compare against internal trade tickets; broker typically authoritative for same-account, same-CUSIP. | `W` if internal flagged and broker missed. |
| `WASH_SALE_AMOUNT_MISMATCH` | Compare exact disallowed $; either side may be authoritative. | `W` with column (g) = internal's value or explanatory statement. |
| `BASIS_OUTSIDE_TOLERANCE` | Use broker's basis if covered; internal's basis + explanatory statement otherwise. | `B` if broker basis matches 1099-B but disagrees with internal. `O` if neither matches jurisdiction. |
| `PROCEEDS_OUTSIDE_TOLERANCE` | Verify against trade ticket / confirmation report. | `O` if non-recoverable. |

## Numerical conventions

- All monetary fields are represented as `decimal.Decimal`, quantized to **cents** (2 fractional digits) at result emission.
- Quantity is represented as `decimal.Decimal` with a working precision of **8 fractional digits** (covers fractional shares to 8 dp — most equity plans).
- Tolerance is dual: **absolute cents** (default `$0.05`) *and* **relative basis-percent** (default `0.0001` = 1 bp). A difference passes if it satisfies *either* bound.
- Date semantics are timezone-naive; date arithmetic compares in the calendar that produced the dates. If a portfolio crosses **DST**, manually anchor to UTC before ingestion.
- Lot IDs must be unique within each side (internal ledger, broker 1099-B). Duplicate IDs raise at ingestion time, not silently.

## Security / PII guidance

Form 1099-B data is **IRS-sensitive PII** (full name, address, partial TIN, dollar positions). When operating on 1099-B data:

- **Encryption at rest** — AES-256 for ledger persistence; KMS-managed keys.
- **Encryption in flight** — TLS 1.2+ (preferably 1.3); no plain-HTTP broker portals.
- **Access logging** — every read of the reconciliation artifact must be audit-logged (`audit-logging-for-configuration-changes` style pattern).
- **Retention** — minimum 7 years post-filing per IRS Pub 583 (sole proprietors) / 3 years per Pub 552 baseline. See `record-keeping-requirements-for-tax-audit-defense` and `record-retention-periods-by-jurisdiction`.
- **No external LLM inference** on raw 1099-B contents; redact lot IDs and identifiers before any LLM-assisted review.
