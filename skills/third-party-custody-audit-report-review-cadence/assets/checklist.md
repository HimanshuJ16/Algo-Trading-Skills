# Institutional Third-Party Custody Audit Review Checklist

## Pre-Review Audit Document Ingestion
- [ ] **SOC 1 Type II Collection**: Obtain latest SOC 1 Type II report (SSAE 18 / ISAE 3402) covering internal financial controls.
- [ ] **SOC 2 Type II Collection**: Obtain latest SOC 2 Type II report covering Security, Availability, and Key Custody controls.
- [ ] **Proof of Reserves (PoR)**: Verify quarterly on-chain Merkle-tree proof of solvency attestation.
- [ ] **Audited Financials**: Review certified balance sheet and capital adequacy statements.

## Audit Opinion & Control Assessment
- [ ] **Auditor Opinion Audit**: Verify SOC opinion is **Unqualified** (Clean). Escalate qualified/adverse opinions to Risk Committee.
- [ ] **Deficiency Analysis**: Review Section IV of SOC report for reported control exceptions in key management or asset segregation.
- [ ] **Coverage Period Validation**: Confirm SOC coverage period covers at least 6 consecutive months.

## Bridge / Gap Letter & CUEC Compliance
- [ ] **Bridge / Gap Letter Verification**: Obtain signed management gap letter if SOC report coverage end date is > 90 days prior to fiscal year-end.
- [ ] **CUEC Audit**: Identify all Complementary User Entity Controls in Section III and verify internal operational implementation.
- [ ] **Risk Rating Calculation**: Execute `evaluate_vendor_compliance()` script and archive annual custody audit memorandum.