# Institutional Third-Party Custody Audit Review Standards

## 1. Audit Report Review Cadence Matrix
| Audit Report Type | Regulatory Framework | Review Cadence | Max Allowed Coverage Gap | Primary Evaluation Focus |
| :--- | :--- | :--- | :--- | :--- |
| **SOC 1 Type II** | SSAE 18 / ISAE 3402 | **Annual** (365 days) | 90 days (Requires Bridge Letter) | Internal financial controls, trade processing, asset segregation |
| **SOC 2 Type II** | AICPA Trust Services Criteria | **Annual** (365 days) | 90 days (Requires Bridge Letter) | Security, Availability, Confidentiality, Key Custody |
| **Proof of Reserves (PoR)** | On-Chain / ZK-Attestation | **Quarterly** (90 days) | 30 days | Merkle-tree proof of solvency, unencumbered reserve verification |
| **ISO / IEC 27001** | ISO Standards | **Annual** (365 days) | N/A | Information Security Management System (ISMS) certification |
| **Financial Audit** | US GAAP / IFRS | **Annual** (365 days) | 120 days | Audited balance sheet, capital adequacy, liquid reserve ratios |

## 2. Complementary User Entity Controls (CUEC / UCC) Standards
Every custodian SOC 1/2 report specifies **CUECs** that the user entity (trading firm) MUST implement internally to maintain valid security controls.
Mandatory institutional CUECs include:
- **Dual-Control Authorization**: Enforce 2-of-3 or 3-of-5 quorum approvals on all external withdrawal requests.
- **Whitelisted Beneficiary Addresses**: Require 48-hour delay and secondary admin approval before adding new withdrawal addresses.
- **API Key & IP Restriction**: Scope API keys to least privilege and restrict ingress to static co-location/datacenter IP ranges.
- **MFA & Hardware Security Tokens**: Mandate WebAuthn/FIDO2 hardware tokens for all custody portal logins.

## 3. Risk Escalation Matrix
- **CRITICAL Risk**: Qualified auditor opinion, Adverse opinion, missing SOC reports (> 12 months), or un-segregated client assets.
  - *Action*: Immediate freeze on new capital allocation; Risk Committee review within 24 hours.
- **HIGH Risk**: SOC report expired (> 365 days) without valid Gap/Bridge letter, or > 2 material control deficiencies reported.
  - *Action*: Formal query to custodian compliance; 30-day remediation countdown.
- **MEDIUM Risk**: Unimplemented internal CUEC controls or Proof of Reserves attestation past due (> 90 days).
  - *Action*: Internal DevOps/Security remediation ticket; bi-weekly tracking.
- **LOW Risk**: All SOC 1/2 Type II reports clean, unqualified, valid gap letters on file, and 100% CUEC implementation verified.