# Institutional VAT/GST Tax Assessment Operations Checklist

## Vendor Invoice Classification & Ingestion
- [ ] **Financial Services Exemption Verification**: Confirm exchange execution, clearing and brokerage fees are categorized as `EXEMPT` (no VAT charged) — UK VATA 1994 Sch 9 Grp 5 / EU Art 135(1).
- [ ] **Exchange Invoice Split**: Split bundled exchange invoices. Membership, admission, port, connectivity and technology charges are `STANDARD_RATED` (`EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE`) even where the execution element is exempt — HMRC VAT Notice 701/49 para 6.9.
- [ ] **Standard-Rated Service Flagging**: Ensure co-location, server hosting, IT feeds and software licences are categorized as `STANDARD_RATED`.
- [ ] **Co-Location Contract Review**: Check whether the contract grants exclusive use of a defined space. If so, the supply may be connected with immovable property and taxable where the data centre sits, not reverse-charged — CJEU C-215/19 *A Oy*.
- [ ] **Vendor Tax Jurisdiction Setup**: Record the supplier's place of belonging to determine domestic vs cross-border status.
- [ ] **Currency Normalisation**: Convert every net amount into a single currency at the correct statutory rate and date *before* ingestion. The engine performs no FX conversion, and the return is filed in the tax jurisdiction's currency.

## Reverse Charge Mechanism (RCM) & Partial Exemption
- [ ] **Reverse Charge Applicability**: UK (VATA 1994 s.8) and EU (Art 196) apply unconditionally. Singapore and Australia (GST Act s.84-5) apply only where the recipient is **not** entitled to a full input tax credit — confirm entitlement against the statutory test, not the modelled ratio alone.
- [ ] **Both RCM Legs Declared**: Self-assessed output VAT (UK Box 1) *and* the restricted input VAT claim (UK Box 4). Reporting only the net effect understates declared output tax.
- [ ] **Specified Supplies in the Numerator**: Exempt Grp 5 supplies to customers outside the UK carry recovery under SI 1999/3121 and belong in the taxable numerator of the ratio.
- [ ] **Statutory Rounding Applied**: Round the pro-rata percentage **up** — to the next whole number (UK reg 101(4); EU Art 175(1)), or to 2 dp where UK reg 101(5) applies. Pass the matching `PartialExemptionRounding` value; do not round for Singapore or Australia.
- [ ] **Ratio Set Before Batch Run**: `set_partial_exemption_ratio()` must be called before `generate_vat_return_summary()`; the batch uses the ratio currently held by the engine.
- [ ] **Unrecoverable Input VAT Expense Allocation**: Post unrecoverable input VAT as a direct operating expense to trading PnL accounts.

## Return Summary & Audit Trail Retention
- [ ] **Period-End Return Summary**: Execute `generate_vat_return_summary()` to compile the purchase-ledger figures.
- [ ] **Sales-Side Output VAT Added**: The engine covers the purchase ledger only; add output VAT on the entity's own supplies before filing.
- [ ] **Warnings Cleared**: Resolve every `assessment.warnings` entry before submission — each one marks a determination the engine could not make on its own.
- [ ] **Tax Authority Audit Retention**: Store `summary.assessments`, source invoices, place-of-supply documentation and PESM agreements for the applicable retention period (6 years in the UK; confirm per jurisdiction).
