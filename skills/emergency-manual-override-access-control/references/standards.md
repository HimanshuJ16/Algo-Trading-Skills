# Standards — emergency-manual-override-access-control

## What is actually mandated, and by whom

Each row below was checked against the primary source. Where no source mandates a
value, the row says so — do not cite this file as authority for a number it calls
a house default.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Immediate emergency cancellation of any or all unexecuted orders | Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) **Art. 12(1)–(3)** | "An investment firm shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected." Unexecuted orders include those of individual traders, desks and, where applicable, clients; the firm must be able to identify which algorithm and trader/desk/client is responsible for each order. | Mandatory, EU |
| Exceptional handling requires risk-function verification **and** a designated individual's authorisation | RTS 6 **Art. 15(6)** | Procedures for handling blocked orders in exceptional circumstances require verification by the risk management function and authorisation by a designated individual, on a trade-by-trade basis. | Mandatory, EU |
| Restrict, identify and trace critical access rights | RTS 6 **Art. 18(5)** | The firm identifies persons with critical IT access rights, restricts their number, and monitors that access for complete traceability. | Mandatory, EU |
| Documented risk management controls and supervisory procedures | **17 CFR 240.15c3-5(b)** | The broker-dealer "shall establish, document, and maintain a system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks of this business activity." | Mandatory, US |
| Reasons for threshold modifications documented and retained | SEC staff **FAQ No. 18** on Rule 15c3-5 | A firm may increase a threshold "in accordance with supervisory procedures. The reasons for such modifications should be documented and retained as part of the broker-dealer's books and records." | Staff guidance, US |
| Annual review and CEO certification of the controls | **17 CFR 240.15c3-5(e)(1)–(2)** | Annual review of market-access business activity; annual CEO (or equivalent) certification that the controls comply with (b) and (c). | Mandatory, US |
| Electronic records preserved WORM **or** with a complete time-stamped audit trail | **17 CFR 240.17a-4(f)**, as amended by Rel. 34-96034 (adopted 12 Oct 2022; effective 3 Jan 2023, broker-dealer compliance date 3 May 2023) | The 2022 amendments retained WORM and added an audit-trail alternative preserving all modifications and deletions with date, time and the individual responsible. | Mandatory, US, where the record is a required book or record |
| Automatic removal/disabling of emergency accounts after a defined period | **NIST SP 800-53 Rev. 5 AC-2(2)** | Automatically remove or disable temporary and emergency accounts after an organisation-defined time period, rather than at an administrator's convenience. | Control framework, voluntary unless contractually imposed |
| Log the use of privileged functions | **NIST SP 800-53 Rev. 5 AC-6(9)** | Audit the execution of privileged functions, to detect misuse by authorised users and by compromised accounts. | Control framework |
| Cryptographic protection of audit information | **NIST SP 800-53 Rev. 5 AU-9(3)** | Implement cryptographic mechanisms to protect the integrity of audit information and audit tools. | Control framework |

**Not found — do not claim it.** RTS 6 contains no article dedicated to recording
manual overrides or staff interventions; Art. 28 governs order records for
high-frequency techniques, not override justifications. The obligation to record
an override's reason is reached in the EU via the Art. 15(6) authorisation
requirement and general MiFID II record-keeping, and in the US via 15c3-5(b) with
FAQ No. 18 — not via a rule that names break-glass logging.

## Cryptographic basis

| Element | Source |
|---|---|
| SHA-256 | FIPS PUB 180-4, *Secure Hash Standard (SHS)*, August 2015 |
| HMAC-SHA-256 (keyed mode, `audit_hmac_key`) | FIPS PUB 198-1, *The Keyed-Hash Message Authentication Code (HMAC)*, July 2008 |
| Hash-chained audit records | B. Schneier and J. Kelsey, "Secure Audit Logs to Support Computer Forensics", *ACM Transactions on Information and System Security* 2(2):159–176, May 1999 |

**Honest limits of the chain.** Hash chaining makes edits, deletions and
reordering detectable *by someone who holds an independent copy of a later hash
or the HMAC key*. Unkeyed SHA-256 over public fields is recomputable by anyone
who can rewrite the store, so an unkeyed chain in a mutable database is
tamper-resistant, not tamper-evident. Use `audit_hmac_key` with the key held
outside the log store, ship records to an append-only sink, or both. This module
implements neither WORM storage nor key management.

## Configuration defaults (calibrate before use)

None of these are regulatory constants. RTS 6 Art. 15(6) requires *a designated
individual*; it names no titles, no quorum size and no duration.

| Parameter | Default | What it does |
|---|---|---|
| `authorized_roles` | `RISK_OFFICER`, `HEAD_TRADER`, `CTO`, `MANAGING_DIRECTOR` | Roles permitted to initiate any override. Replace with your firm's designated individuals. |
| `critical_actions` | `KILL_SWITCH_ALL_ALGOS` | Actions requiring four-eyes or break-glass. Anything unlisted is `SEVERITY_HIGH` and needs one authorised operator — enumerate every firm-wide action. |
| `critical_approver_roles` | `None` (falls back to `authorized_roles`) | Narrows who may initiate or co-sign a critical action. |
| `max_ttl_minutes` | `60` | Upper bound on how long one override stays in force. A house default: no regulator publishes a maximum break-glass duration. Choose it from how long your desk can operate with the control suppressed. |
| `min_justification_chars` | `10` | Floor on justification length. A length check is a completeness check, not a quality check — it cannot tell a reason from ten arbitrary characters, so pair it with retrospective review. |
| `audit_hmac_key` | `None` (unkeyed SHA-256) | Set it in production; see the honest limits above. |

## Known limitations

- **In-memory reference adapters.** `active_overrides`, `audit_chain` and
  `BreakGlassTokenRegistry` do not survive a restart. A restart silently drops
  every active override *and* its pending expiry.
- **Authorisation only.** The engine neither cancels orders nor blocks order
  entry; an approved report must reach an executor.
- **Trusted identity assumed.** Roles and operator ids are taken as
  authenticated. The engine cannot detect a forged claim.
- **No MFA / step-up authentication.** Multi-factor verification of the operator
  belongs to the IAM layer in front of this engine.

## Category

`infrastructure-security`
