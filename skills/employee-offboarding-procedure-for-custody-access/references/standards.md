# Standards for Employee Offboarding for Custody Access

## What the sources actually require

No regulator or standards body publishes a numeric deadline for revoking custody
access or rotating a departing signer's key material. Every citation below sets an
obligation of *promptness* and leaves the clock to the organisation.

| Source | Obligation | What it does NOT say |
|---|---|---|
| NIST SP 800-53 Rev. 5, **PS-4 (Personnel Termination)** | Disable system access **within an organisation-defined time period**; terminate/revoke authenticators and credentials; retrieve security-related property. PS-4(2) covers automated termination notification. | Does not fix the time period. The organisation defines it. |
| AICPA Trust Services Criteria (2017, revised points of focus 2022), **CC6.2 / CC6.3** — the SOC 2 security criteria | Access is removed when a user is no longer authorised; access to data, software and functions is authorised, modified or removed in line with documented policy. Auditors test by sampling the termination list for still-active accounts. | Does not define "timely" in hours. |
| **23 NYCRR 500.7(a)(6)** (NYDFS Part 500, second amendment effective 2023-11-01; §500.7 compliance date 2025-05-01) | Covered entities must "promptly terminate access following departures." | Does not quantify "promptly". Applies only to NYDFS covered entities — not to every crypto fund. Confirm your entity's status before citing it. |
| **PCI DSS v4.0 requirement 8.2.5** | "Access for terminated users is immediately revoked." | Binds only entities in cardholder-data-environment scope. Cited here as the one mainstream standard that says *immediately*; it is an analogue for a trading firm, not an obligation, unless you are in PCI scope. |
| NIST SP 800-88 Rev. 1 (*Guidelines for Media Sanitization*) | Clear / Purge / Destroy decision framework for returned devices, with a sanitisation record. | Sanitising a device says nothing about copies of a seed phrase made off-device. |

## Engine defaults

| Setting | Default | Basis |
|---|---|---|
| `credential_revocation_sla_hours` | `0.0` — any credential step still pending after the effective termination time is overdue | Encodes "immediately"/"promptly" literally rather than inventing a grace period. Raise it only to a window the firm has documented. |
| `key_rotation_sla_hours` | `24.0` | **Firm policy default with no regulatory basis.** Calibrate against how long a re-key or MPC reshare actually takes on your platform. |

## Scoring rules

| Rule | Behaviour |
|---|---|
| Denominator | Applicable steps only. A waived step leaves the denominator and must carry a written justification. |
| Never waivable | `IDP_SSO_REVOKED` always applies; `MULTISIG_MPC_KEY_ROTATED` cannot be waived while `held_custody_keys` is True. |
| Unrecognised step | Raises `CustodyOffboardingError`. It is never counted as progress. |
| SLA boundary | Exclusive: overdue requires `elapsed > sla`, so exactly-at-SLA is not yet a breach. |
| Future-dated termination | Negative elapsed hours; nothing overdue. |
| Risk precedence | `CRITICAL_KEY_EXPOSURE_RISK` > `HIGH_CREDENTIAL_EXPOSURE_RISK` > `ELEVATED_ROTATION_PENDING` > `PENDING_LOW_RISK` > `LOW_RISK`. |

## Sources

- NIST SP 800-53 Rev. 5, control PS-4 — https://csf.tools/reference/nist-sp-800-53/r5/ps/ps-4/
- AICPA 2017 Trust Services Criteria (with 2022 revised points of focus), CC6.2/CC6.3 — https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022
- 23 NYCRR § 500.7, Access privileges and management — https://www.law.cornell.edu/regulations/new-york/23-NYCRR-500.7
- PCI DSS v4.0.1, requirement 8.2.5 — https://www.pcisecuritystandards.org/document_library/ (PCI DSS v4.0.1, "Requirements and Testing Procedures")
- NIST SP 800-88 Rev. 1, Guidelines for Media Sanitization — https://csrc.nist.gov/pubs/sp/800/88/r1/final
