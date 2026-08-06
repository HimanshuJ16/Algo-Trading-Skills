---
name: third-party-custody-audit-report-review-cadence
description: "Institutional audit review engine for third-party crypto & asset custodians, enforcing annual SOC 1/2 Type II reviews, gap letter coverage, Proof of Reserves attestations, and CUEC control compliance."
domain: Crypto Custody
subdomain: Security
tags:
- custody
- audit
- soc1
- soc2
- proof-of-reserves
- cuec
- risk-management
- compliance
brokers_frameworks:
- fireblocks
- bitgo
- coinbase-custody
- bny-mellon
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when managing institutional relationships with third-party crypto custodians, prime brokers, asset sub-custodians, or fiat custody banks holding trading capital or client assets.

This skill provides automated governance for:
- Establishing structured audit report review schedules (SOC 1 Type II, SOC 2 Type II, ISO 27001, Proof of Reserves).
- Validating auditor opinions (Unqualified vs Qualified/Adverse) and tracking reported control deficiencies.
- Monitoring bridge/gap letter validity when custodian audit reporting cycles lag the firm's fiscal year-end.
- Verifying implementation of Complementary User Entity Controls (CUECs / UCCs) required by custodians.
- Triggering automatic risk rating escalations and capital allocation caps upon audit deficiencies.

## Prerequisites

- Python 3.9+
- Access to third-party custodian compliance portals or vendor management repositories.
- Internal risk committee governance guidelines for custody risk management (SEC Rule 206(4)-2, MiCA Article 75, FCA CASS 6).

## Workflow

1. **Register Custody Vendor**: Register vendor profile via `register_vendor()` in `CustodyAuditReviewEngine` with AUM, held asset classes, and required review cadence (e.g. 365 days for SOC 1/2, 90 days for Proof of Reserves).
2. **Ingest Audit Reports**: Submit newly released audit reports via `submit_audit_report()` specifying report type, auditor opinion (Unqualified vs Qualified), coverage start/end dates, and identified deficiencies.
3. **Ingest Bridge / Gap Letters**: If the SOC report coverage period has ended, submit gap letters via `submit_gap_letter()` to maintain continuous audit coverage.
4. **Audit CUEC Implementation**: Verify internal implementation of vendor-mandated Complementary User Entity Controls (e.g. multi-user withdrawal signoffs, IP whitelisting) via `update_cuec_checks()`.
5. **Evaluate Compliance & Risk**: Invoke `evaluate_vendor_compliance()` to compute vendor status (`COMPLIANT`, `OVERDUE`, `NON_COMPLIANT`, `ESCALATED`), calculate risk ratings (`LOW` to `CRITICAL`), and set next due dates.
6. **Escalation & Overdue Audits**: Call `get_overdue_vendors()` to flag overdue vendor reviews for Risk Committee escalation.

## Common Pitfalls

- **Ignoring Complementary User Entity Controls (CUECs)**: A clean SOC 2 report relies on the user entity enforcing internal controls (e.g., API key security, IP whitelisting). Failing to implement CUECs invalidates custody security guarantees.
- **Relying on SOC 1 / SOC 2 Type I Reports**: Type I reports only evaluate control design at a single point in time. Institutional custody compliance mandates **Type II** reports evaluating control operational effectiveness over at least 6 months.
- **Unmonitored Audit Coverage Gaps**: Allowing gaps (> 6 months) between audit report coverage dates without a valid Bridge/Gap Letter exposes the firm to unmonitored custodian control drift.
- **Overlooking Qualified Auditor Opinions**: Failing to escalate qualified audit opinions (e.g., missing asset segregation controls) can lead to catastrophic custody losses during custodian insolvency.

## Verification

Execute the unit test suite to validate SOC opinion evaluation, gap letter coverage, CUEC auditing, and risk scoring:

```bash
python -m unittest discover -s skills/third-party-custody-audit-report-review-cadence/scripts
```

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `vendor-lock-in-risk-for-proprietary-custody-formats`
- `air-gapped-signing-workflow-for-cold-storage`
- `exchange-proof-of-reserves-verification`

