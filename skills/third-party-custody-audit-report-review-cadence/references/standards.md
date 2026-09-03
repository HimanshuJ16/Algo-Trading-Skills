# Institutional Third-Party Custody Audit Review Standards

> **What is a standard here and what is not.** Almost none of the cadences below
> come from a standard. The AICPA SOC guidance does not address bridge letters and
> sets no minimum Type II observation period; Proof of Reserves has no mandated
> cadence at all. Every "Review Cadence" and "Max Coverage Gap" figure in the matrix
> is a **firm policy default** implemented in `scripts/`, and each row states what,
> if anything, an external source actually fixes.

## 1. Audit Report Review Cadence Matrix

| Audit Report Type | Underlying framework | Review cadence (firm policy) | Max coverage gap (firm policy) | Primary evaluation focus |
| :--- | :--- | :--- | :--- | :--- |
| **SOC 1 Type II** | AICPA AT-C 320 (from SSAE No. 18); ISAE 3402 internationally | Annual (365 days) | 90 days, bridged by a signed management bridge letter | Internal control over financial reporting: trade processing, asset segregation, reconciliation |
| **SOC 2 Type II** | AICPA Trust Services Criteria, examined under AT-C 105/205 | Annual (365 days) | 90 days, bridged by a signed management bridge letter | Security, Availability, Confidentiality; key custody and access control |
| **Proof of Reserves** | None — no standard, no mandated cadence | Quarterly (90 days) | 30 days | Point-in-time reserve snapshot only. **Not an audit** — see §4 |
| **ISO/IEC 27001:2022** | ISO/IEC 27001; certification governed by ISO/IEC 17021-1 and ISO/IEC 27006 | Certificate runs a **3-year cycle** with surveillance audits in years 1 and 2 and recertification before year 3. Review the current surveillance report annually and track certificate expiry | N/A — a certificate is not period coverage | ISMS certification scope: check the Statement of Applicability actually covers the custody service |
| **Financial audit** | US GAAP / IFRS | Annual (365 days) | 120 days | Audited balance sheet, capital adequacy, liquid reserve ratios |

Staleness is always measured from the **coverage end date**, never from the report
issue date: a report issued in February for a period ending the prior December is
already two months stale on arrival.

## 2. Bridge (Gap) Letters — what they are worth

A bridge letter (gap letter) covers the interval between a SOC report's coverage end
and the user entity's own reporting date. Three properties decide how much weight to
give it:

- It is signed by the **service organisation's management**, not by the service
  auditor. The auditor's examination ended with the report period and it cannot
  attest to anything after it.
- It therefore provides **no audit assurance**. It asserts only that management is
  not aware of material changes in the control environment, and normally carries an
  explicit disclaimer that it does not replace a SOC report.
- The AICPA SOC guidance does not cover bridge letters. The convention that a bridge
  letter should span **no more than about three months** is practitioner consensus,
  not a requirement — which is exactly why the window is configurable
  (`max_unbridged_gap_days`, default 90).

Consequence for review: coverage is scored on two independent axes.

1. **Has the report expired?** Measured as days since coverage end against
   `review_cadence_days`. Bridging is a three-month device, so a bridge letter can
   never cure a report that has blown the annual cadence: the vendor is **OVERDUE /
   HIGH** whatever letters are on file.
2. **How much time is covered by nothing?** Measured from the later of the coverage
   end and the end of the best valid bridge letter, against `max_unbridged_gap_days`.
   Exceeding it is **MEDIUM**.

Relying on a bridge letter at all caps the vendor at **MEDIUM**, because the period
it covers is management's assertion rather than an audited one.

A bridge letter is accepted only when it names the SOC report it bridges, carries a
signature dated on or after its own period end, asserts no material changes, starts
no later than the day after coverage ended (otherwise a window is left unbridged),
and does not attest to a future period. An accepted letter need not reach today — it
stops bridging where it ends, and the remaining window is scored in axis 2.

## 3. Subservice organisations: carve-out vs inclusive method

Where the custodian's report uses the **carve-out method**, the subservice
organisation's services are excluded from the system description *and from the scope
of the examination*. The report discloses the **Complementary Subservice
Organisation Controls (CSOCs)** it assumes the subservice organisation operates, but
the service auditor does not test them. If a custodian carves out its cloud
provider, HSM vendor or MPC co-signer, the report on file evidences nothing about
those controls: obtain the subservice organisation's own SOC report covering an
overlapping period, and confirm it addresses the CSOCs the custodian's report names.
Under the **inclusive method** the subservice organisation is inside the scope and
no separate report is needed.

## 4. Complementary User Entity Controls (CUEC / UCC)

A custodian's SOC 1/SOC 2 report specifies CUECs — controls the **user entity** must
operate for the report's control objectives or criteria to be met. A clean opinion is
conditional on them. Institutional CUECs typically include:

- **Dual-control authorisation** — quorum approval (e.g. 2-of-3, 3-of-5) on every
  external withdrawal request.
- **Whitelisted beneficiary addresses** — time-delayed addition of new withdrawal
  addresses with secondary administrative approval.
- **API key and IP restriction** — least-privilege key scoping, ingress restricted to
  known static ranges.
- **MFA and hardware security tokens** — WebAuthn/FIDO2 authenticators for custody
  portal access.

Transcribe the report's CUEC list into `AuditReport.cuecs_required`. An empty list is
almost always an untranscribed section rather than a custodian that requires nothing,
so the engine reports **not assessed** instead of scoring 100%. A control recorded as
implemented with no verification evidence is likewise treated as not implemented.

## 5. Risk escalation matrix

As implemented in `scripts/`. Ratings are monotonic: a later check may raise the
rating, never lower one an earlier check set.

- **CRITICAL** — qualified, adverse or disclaimed auditor opinion; or no SOC 1/SOC 2
  Type II report on file at all (including a vendor holding only Proof of Reserves or
  ISO certificates).
  - *Action*: freeze new capital allocation; Risk Committee review within 24 hours.
    Surfaced by `get_vendors_requiring_escalation()`.
- **HIGH** — SOC coverage stale beyond `review_cadence_days` (no bridge letter
  cures this); **any** deficiency reported in the test results; a Type II
  observation period shorter than `min_type2_coverage_days`; or audit dates that are
  internally inconsistent (coverage ending after the evaluation date).
  - *Action*: formal query to custodian compliance; 30-day remediation countdown.
- **MEDIUM** — coverage resting on a bridge letter rather than an audited report;
  an unbridged window beyond `max_unbridged_gap_days`; unimplemented, unevidenced or
  unassessed CUECs; Proof of Reserves past `por_cadence_days` or absent where
  required.
  - *Action*: internal remediation ticket; bi-weekly tracking.
- **LOW** — a clean unqualified Type II report inside cadence with no unbridged
  window, meeting the firm's observation-period floor, and every required CUEC
  implemented and evidenced.

## 6. Regulatory context — and its limits

None of the following obliges a firm to collect a SOC report from an *unaffiliated*
third-party custodian. Cite them for what they do say:

| Instrument | What it actually requires |
| :--- | :--- |
| **17 CFR 275.206(4)-2(a)(6)(ii)** (Advisers Act custody rule) | Where the adviser **or a related person** is the qualified custodian, the adviser must obtain or receive, **at least once each calendar year**, an internal control report relating to custody services, prepared by an independent public accountant **registered with and subject to inspection by the PCAOB**. This is the one hard annual SOC-style obligation in scope — and it does not reach unaffiliated custodians. The 2023 "Safeguarding Advisory Client Assets" proposal that would have expanded the rule was formally withdrawn by the SEC on 2025-06-12; rule 206(4)-2 remains in force unamended. |
| **MiCA (Regulation (EU) 2023/1114) Article 75** | Governs custody and administration of crypto-assets for clients: client agreement, custody policy, register of positions, segregation, liability for ICT-related losses. It does not mandate a SOC report, an external custody audit, or insurance. |
| **FCA CASS 6** | UK custody rules for safeguarding and administering client assets: registration, recordkeeping and reconciliation. The annual client-assets audit obligation sits in SUP 3.10, not CASS 6. Safeguarding *cryptoassets* does not become a UK regulated activity until 2027. |

## 7. Sources

- AICPA AT-C section 320 (SOC 1, from SSAE No. 18); AT-C 105/205 as revised by SSAE
  No. 21; SSAE No. 23 quality-management amendments effective for engagements
  beginning on or after 2025-12-15.
- PCAOB Office of the Investor Advocate, *Investor Advisory: Exercise Caution With
  Third-Party Verification/Proof of Reserve Reports*, 2023-03-08 —
  https://pcaobus.org/resources/information-for-investors/investor-advisories/investor-advisory-exercise-caution-with-third-party-verification-proof-of-reserve-reports
- Linford & Co., *What are Bridge (Gap) Letters in SOC Reports?* — signed by service
  organisation management, no audit assurance, ~3-month practice, no AICPA guidance:
  https://linfordco.com/blog/gap-bridge-letter/
- 17 CFR 275.206(4)-2, Custody of funds or securities of clients by investment
  advisers.
- SEC, *Withdrawal of Proposed Regulatory Actions*, Release 33-11377, 2025-06-12 —
  https://www.sec.gov/files/rules/final/2025/33-11377.pdf
- ISO/IEC 27001:2022; certification cycle governed by ISO/IEC 17021-1 and ISO/IEC
  27006 (3-year certificate, annual surveillance audits).
- Regulation (EU) 2023/1114 (MiCA), Article 75; FCA Handbook CASS 6 and SUP 3.10.
