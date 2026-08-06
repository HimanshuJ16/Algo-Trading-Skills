# Institutional VAT/GST Tax Assessment Operations Checklist

## Vendor Invoice Classification & Ingestion
- [ ] **Financial Services Exemption Verification**: Confirm exchange execution, clearing, and brokerage fees are categorized as `EXEMPT` (no VAT charged).
- [ ] **Standard-Rated Service Flagging**: Ensure co-location, server hosting, IT feeds, and software licenses are categorized as `STANDARD_RATED`.
- [ ] **Vendor Tax Jurisdiction Setup**: Record vendor tax registration country to determine domestic vs cross-border status.

## Reverse Charge Mechanism (RCM) & Partial Exemption
- [ ] **Cross-Border RCM Self-Assessment**: Self-assess output VAT and input VAT for imported cross-border services (e.g. US software to UK entity).
- [ ] **Partial Exemption Pro-Rata Ratio**: Calculate annual recovery ratio $\frac{\text{Taxable Supplies}}{\text{Taxable Supplies} + \text{Exempt Supplies}}$.
- [ ] **Unrecoverable Input VAT Expense Allocation**: Post unrecoverable input VAT as a direct operating expense to trading PnL accounts.

## Return Summary & Audit Trail Retention
- [ ] **Period-End Return Summary**: Execute `generate_vat_return_summary()` to compile quarterly tax filings.
- [ ] **Tax Authority Audit Retention**: Store invoice tax assessment records, place-of-supply documentation, and PESM agreements for 6 years.