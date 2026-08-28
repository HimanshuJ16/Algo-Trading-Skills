# Standards — Segregation of Duties for Custody Operations

## Status of these requirements

**Every number in this skill is firm policy, not regulation.** The
`$50,000` threshold, the 1-approval and 2-approval tiers, and the choice of
SHA-256 are module defaults. No source surveyed below prescribes a dollar
threshold, an approval count, or a hash algorithm for a custody transfer
approval. Earlier versions of this skill stated the opposite — that large
transfers "MUST require at least 2 distinct approvals" and that all approvals
"MUST record SHA-256 cryptographic signatures" for SOC 2. Neither is a
requirement of any framework; both were removed.

What *is* externally grounded is the **shape** of the control: segregate
incompatible duties, do not let one person complete a sensitive transaction
alone, keep administration separate from audit, and grant access by role.

Nothing here is legal advice. Whether a firm is in scope of any of the regimes
below is a question for counsel.

## SOC 2 — what the Trust Services Criteria actually say

SOC 2 is an **attestation engagement** performed by a CPA firm against the AICPA
Trust Services Criteria. It is not a certification, and the TSC are criteria, not
a control catalogue with thresholds. Three places carry segregation of duties:

| Ref | Register | Text |
|---|---|---|
| **CC1.3** (COSO Principle 3) | Point of focus | "**Defines, Assigns, and Limits Authorities and Responsibilities** — Management and the board of directors delegate authority, define responsibilities, and use appropriate processes and technology to assign responsibility and **segregate duties** as necessary at the various levels of the organization." |
| **CC5.1** (COSO Principle 10) | Point of focus | "**Addresses Segregation of Duties** — Management segregates incompatible duties, and where such segregation is not practical, management selects and develops alternative control activities." |
| **CC6.3** | Criterion text | "The entity authorizes, modifies, or removes access to data, software, functions, and other protected information assets based on roles, responsibilities, or the system design and changes, giving consideration to the concepts of **least privilege and segregation of duties**, to meet the entity's objectives." Its point of focus: "**Uses Role-Based Access Controls** — Role-based access control is utilized to support segregation of incompatible functions." |

The TSC definition of *unauthorized access* also turns on this concept: access
that "(a) has not been approved by a person designated to do so by management and
(b) **compromises segregation of duties**, confidentiality commitments, or
otherwise increases risks … beyond the levels approved by management".

Two consequences for how this engine is described to an auditor:

- CC5.1 is a **point of focus**, which the TSC treat as guidance for applying the
  criterion, not as a checklist item that must be satisfied verbatim. CC6.3 is
  criterion text and is therefore the stronger hook for the RBAC role-conflict
  matrix.
- "Where such segregation is not practical, management selects and develops
  alternative control activities" is an explicit escape hatch. A small firm that
  genuinely cannot staff a separate checker is expected to compensate and
  document, not to fake a second approver id.

Source: [AICPA, *TSP Section 100, 2017 Trust Services Criteria for Security,
Availability, Processing Integrity, Confidentiality, and
Privacy*](https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022).

## NIST SP 800-53 Rev. 5 — AC-5 Separation of Duties

The control requires organisations to identify and document the duties of
individuals requiring separation, and to define system access authorisations to
support that separation. Its discussion is the clearest statement of *why* the
role-conflict matrix in this skill looks the way it does:

> Separation of duties addresses the potential for abuse of authorized privileges
> and helps to reduce the risk of malevolent activity **without collusion**.
> Separation of duties includes dividing mission or business functions and
> support functions among different individuals or roles, conducting system
> support functions with different individuals, and **ensuring that security
> personnel who administer access control functions do not also administer audit
> functions**.

| AC-5 concept | Modelled as |
|---|---|
| Admin must not also audit | `SECURITY_ADMIN` + `AUDITOR` in `DEFAULT_INCOMPATIBLE_ROLE_PAIRS` |
| Duties divided among different individuals | `SECURITY_ADMIN` + `INITIATOR` / `APPROVER` conflicts; maker-checker self-approval block |
| Enforced through access authorisations | `register_user` screens the matrix *before* storing the identity |
| Reduces malevolent activity **without collusion** | The honest limit of this engine: two colluding registered people defeat every control here |

AC-5 mandates *that* duties be separated. It prescribes no approval count and no
threshold. Note also AC-5's dependency chain — it is enforced through the account
management of AC-2 and the access enforcement of AC-3; an engine-level matrix is
worth little if the identity provider hands out roles unscreened.

Source: [NIST SP 800-53 Rev. 5,
AC-5](https://csf.tools/reference/nist-sp-800-53/r5/ac/ac-5/) ·
[publication](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final).

## BCBS — dual control as an operational risk principle

The Basel Committee's *Revisions to the principles for the sound management of
operational risk* (BCBS d515, March 2021), Principle 9, paragraph 50, is the
clearest supervisory statement of the maker-checker rationale:

> An effective control environment also requires appropriate segregation of
> duties. Assignments that establish conflicting duties for individuals or a
> team, **without dual controls (eg a process that uses two or more separate
> entities (usually persons) operating in concert to protect sensitive functions
> or information) or other countermeasures, may result in concealment of losses,
> errors or other inappropriate actions**. Therefore, areas where conflicts of
> interest may arise should be identified, minimised, and be subject to careful
> independent monitoring and review.

Paragraph 51 lists "clearly established authorities and/or processes for
approval" among the traditional internal controls that sit alongside segregation
of duties and dual control.

Scope caveat: BCBS principles apply to **banks**, through each jurisdiction's
implementation. A crypto fund or a proprietary trading firm is not in scope. The
paragraph is cited here as the authoritative articulation of the control's
purpose, not as an obligation on the reader.

Source: [BCBS d515, *Revisions to the principles for the sound management of
operational risk*, March 2021](https://www.bis.org/bcbs/publ/d515.pdf).

## NYDFS — where an actual obligation may attach

A New York virtual currency licensee (23 NYCRR Part 200) is also a covered entity
under the NYDFS cybersecurity regulation, 23 NYCRR Part 500. Section **500.7
(Access privileges and management)**, as amended 1 November 2023, is the nearest
binding requirement to what this engine does. It requires a covered entity to
limit user access privileges to information systems providing access to nonpublic
information, limit the number and function of privileged accounts, limit use of
privileged accounts to when those functions are being performed, review all user
access privileges **at a minimum annually** and remove or disable accounts and
access no longer necessary, and promptly terminate access following departures.
Class A companies additionally must monitor privileged access activity and
implement a privileged access management solution.

This is a **least-privilege and access-review obligation, not a maker-checker
obligation** — 500.7 does not require dual approval of a transfer. Cite it for
the roster hygiene around this engine (registration, revocation, annual review),
not for the approval quorum. Compliance dates for the 2023 amendments were phased,
with 500.7 falling in the 1 May 2025 tranche.

Sources: [23 NYCRR 500.7 (Cornell
LII)](https://www.law.cornell.edu/regulations/new-york/23-NYCRR-500.7) ·
[NYDFS Part 500](https://www.dfs.ny.gov/industry-guidance/cybersecurity).

## Recordkeeping — do not assume a retention period

Nothing in this skill establishes how long an approval record must be kept. The
applicable period depends on the entity: a US broker-dealer looks to SEA Rule
17a-4; an EU/UK investment firm to MiFID II and Delegated Regulation (EU)
2017/565 Article 72; a NYDFS licensee to Part 200. See
`record-retention-periods-by-jurisdiction` and confirm with counsel rather than
copying a number from a code comment.

On immutability specifically: an in-process SHA-256 chain is tamper-**evident**,
not non-rewriteable. It satisfies neither the WORM alternative nor the
audit-trail alternative of SEA Rule 17a-4(f) on its own — the preservation
guarantee has to come from the storage layer. `risk-control-bypass-audit-logging`
covers that ground in detail.

## Engineering standards enforced by this engine

| Property | Rule | Enforced by |
|---|---|---|
| Maker-checker | The initiator can never approve their own proposal; checked first, so the error names the SoD breach rather than a role error | `approve_transfer` step 1 |
| Role-conflict matrix | Screened before the identity is stored; a rejected registration leaves no user and does not advance the audit chain | `_screen_role_conflicts` / `register_user` |
| Immutable role snapshot | Roles are copied into a `frozenset`; mutating the caller's set cannot grant privileges inside the engine | `register_user` |
| Explicit role change | Re-registering an existing `user_id` raises unless `replace=True` | `register_user` |
| Payload binding | Approvals carry the digest of the exact payload reviewed; a changed field strands them and the status falls back to `PENDING` | `compute_proposal_digest` / `valid_approvals` / `refresh_status` |
| Unambiguous digest | Every field length-prefixed under a domain separator, so no field-boundary shuffle collides | `_length_prefixed` |
| Threshold inside the digest | Lowering `required_approvals` after the fact invalidates the approvals it was lowered under | `compute_proposal_digest` |
| Fail loudly on notionals | NaN, Inf, zero and negative amounts raise rather than silently landing in the low tier | `_require_positive_amount` |
| Idempotent proposals | Identical resubmission returns the existing proposal with approvals intact; conflicting reuse of an id raises | `propose_transfer` |
| Execute once | `mark_executed` re-derives status, refuses anything not `APPROVED`, and refuses a second call | `mark_executed` |
| Auditable rejection | Every refusal carries a machine-readable `SoDViolationType` | `SoDConflictError.violation_type` |
| Tamper evidence | Every lifecycle event is a SHA-256 link over its predecessor; `verify_audit_chain` names the offending sequence | `_append_audit` / `verify_audit_chain` |
| Thread safety | All state mutation is under a re-entrant lock | `threading.RLock` |
| Reproducibility | Injectable clock, so an audit chain can be regenerated deterministically | `clock=` |

## What this engine deliberately does not claim

- It does not verify that the named approver approved anything.
  `signature_hash` is an unkeyed chain link, not a signature.
- It does not enforce anything at the vault. The custodian's policy engine, the
  HSM quorum, or the on-chain threshold is the authoritative control.
- It does not defend against collusion between two registered, distinct,
  correctly-roled people. AC-5 says as much: separation of duties reduces
  malevolent activity *without collusion*.
- It holds no cross-process state, so it cannot prevent a double release by two
  independent workers.

## Sources

- AICPA, TSP Section 100, 2017 Trust Services Criteria (with revised points of focus, 2022) — https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022
- NIST SP 800-53 Rev. 5, AC-5 Separation of Duties — https://csf.tools/reference/nist-sp-800-53/r5/ac/ac-5/
- NIST SP 800-53 Rev. 5 (publication) — https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final
- BCBS d515, Revisions to the principles for the sound management of operational risk (March 2021) — https://www.bis.org/bcbs/publ/d515.pdf
- 23 NYCRR 500.7, Access privileges and management (amended 1 Nov 2023) — https://www.law.cornell.edu/regulations/new-york/23-NYCRR-500.7
- NYDFS cybersecurity regulation resources — https://www.dfs.ny.gov/industry-guidance/cybersecurity
- NYDFS virtual currency business licensing (23 NYCRR Part 200) — https://www.dfs.ny.gov/virtual_currency_businesses
