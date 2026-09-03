---
name: third-party-custody-audit-report-review-cadence
description: >-
  Audit-evidence review cadence engine for third-party crypto and asset
  custodians: tracks SOC 1/SOC 2 Type II coverage against a configurable review
  cadence, validates auditor opinions, accepts or rejects management bridge (gap)
  letters on their real evidential weight, checks Proof of Reserves freshness, and
  scores the firm's own implementation of the report's Complementary User Entity
  Controls. Fails closed: unassessed evidence is never scored as compliant.
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
- AICPA SOC 1 (AT-C 320) / SOC 2
- SEC Rule 206(4)-2
- PCAOB Investor Advisory 2023-03-08
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a firm holds trading capital or client assets with third-party
custodians, prime brokers, sub-custodians or fiat custody banks, and someone has to
be able to answer — on a dated, auditable basis — *what audit evidence do we
currently hold on this custodian, and how stale is it?*

It governs the evidence file, not the custodian:

- Tracking SOC 1 Type II / SOC 2 Type II coverage against a configurable review
  cadence, and flagging expiry.
- Recording auditor opinions (unqualified vs qualified / adverse / disclaimed) and
  the control deficiencies reported in the test-results section.
- Deciding whether a bridge (gap) letter actually bridges the period since the
  report's coverage ended — the right report, signed, contiguous, and recent.
- Checking Proof of Reserves attestation freshness against a separate cadence.
- Verifying that the firm has implemented the Complementary User Entity Controls
  (CUECs) the custodian's report places on it, with evidence.
- Producing a risk rating (`LOW` → `CRITICAL`) and status (`COMPLIANT`, `OVERDUE`,
  `NON_COMPLIANT`, `ESCALATED`) that a risk committee can act on.

The engine fails closed. Evidence that has not been assessed is reported as
unassessed, never scored as satisfied.

## When NOT to Use

- **To choose a custodian.** This is periodic-review governance over an existing
  relationship. Initial selection — qualified-custodian status, bankruptcy
  remoteness, insurance, key management — belongs to
  `custody-solution-vendor-due-diligence-checklist`.
- **To decide whether a custody arrangement is lawful in a jurisdiction.** Use
  `regulatory-custody-requirements-by-jurisdiction`; nothing here is legal advice,
  and the cadences below are firm policy, not rules (see Prerequisites).
- **To verify a Proof of Reserves publication.** This skill only tracks how old the
  attestation is. Cryptographically verifying a Merkle sum tree is
  `exchange-proof-of-reserves-verification`.
- **To read or interpret a SOC report.** The engine records the conclusion a human
  reviewer reached; it does not parse Section III/IV, and it cannot tell you
  whether a control objective was met.
- **As a real-time control.** Output is a periodic governance verdict, not a
  pre-trade or pre-withdrawal check. Withdrawal-time enforcement belongs to
  `multi-signature-approval-for-large-transfers` and
  `exchange-withdrawal-whitelist-enforcement`.

## Prerequisites

- Python 3.9+ (standard library only).
- Access to custodian compliance portals or a vendor-management repository holding
  the SOC reports, bridge letters and attestations themselves.
- A documented internal policy fixing the cadences, because **no standard supplies
  them**:
  - The AICPA SOC guidance does not address bridge letters at all; the ~3-month
    bridging limit implemented as `max_unbridged_gap_days=90` is industry
    practice.
  - The AICPA sets no minimum Type II observation period; periods of 3–12 months
    occur in practice. `min_type2_coverage_days=180` is a firm-policy floor.
  - Proof of Reserves has no mandated cadence at all.
- Correct scoping of the regulatory hook. Advisers Act rule **17 CFR
  275.206(4)-2(a)(6)(ii)** requires an internal control report from a
  PCAOB-registered and PCAOB-inspected accountant at least once each calendar year
  only where the adviser **or a related person** is the qualified custodian. For an
  unaffiliated custodian, the annual cadence enforced here is the firm's own
  policy. **MiCA Article 75** (custody and administration of crypto-assets) and
  **FCA CASS 6** govern how custody is conducted; neither obliges a firm to collect
  a SOC report from its custodian.

## Workflow

1. **Register the vendor** — `register_vendor()` with AUM, asset classes and the
   firm's cadences (`review_cadence_days`, `max_unbridged_gap_days`,
   `por_cadence_days`, `min_type2_coverage_days`,
   `requires_proof_of_reserves`). Re-registering an existing `vendor_id` raises
   unless you pass `replace=True`, because overwriting silently would discard every
   report, letter and CUEC check already recorded against that vendor.
2. **Ingest audit reports** — `submit_audit_report()` per artefact, with report
   type, opinion, coverage start/end, report date, deficiency count, and
   `cuecs_required` transcribed from the report's CUEC section. Populating
   `cuecs_required` is what lets step 5 tell *"CUEC not implemented"* apart from
   *"CUEC never assessed"*. Duplicate `report_id` values are rejected.
3. **Ingest bridge / gap letters** — `submit_gap_letter()` when the coverage period
   has ended. Record the letter as received, including a defective one: validity is
   decided at evaluation time and every rejection reason lands in the audit trail
   rather than disappearing at ingestion. A letter is accepted only if it names the
   SOC report it bridges, carries a `signed_date` on or after its own period end,
   asserts no material changes, starts no later than the day after coverage ended,
   and does not attest to the future. An accepted letter does not *have* to reach
   today — it simply stops bridging where it ends, and step 5 scores whatever
   remains uncovered.
4. **Audit CUEC implementation** — `update_cuec_checks()` with one `CUECCheck` per
   control. A control marked implemented with blank `verification_evidence` counts
   as unevidenced, i.e. not implemented.
5. **Evaluate** — `evaluate_vendor_compliance(vendor_id, current_date)`. Always
   pass `current_date` explicitly: the verdict is a dated statement, and passing it
   makes the review reproducible. The call is side-effect free. Risk is monotonic —
   a later check can raise the rating, never lower one an earlier check set.
   Coverage is judged on two independent axes: whether the report itself has
   expired against `review_cadence_days` (`OVERDUE`, `HIGH`), and how many days
   since the last audited *or bridged* date remain covered by nothing, against
   `max_unbridged_gap_days` (`MEDIUM`). A bridge letter can close the second; it
   can never cure the first.
6. **Record and escalate** — `record_review()` stamps that a review was actually
   performed (deliberately separate from evaluating, so listing vendors never
   rewrites review history). `get_overdue_vendors()` returns vendors that are
   `OVERDUE` or `NON_COMPLIANT`; a qualified opinion makes a vendor `ESCALATED`
   instead, so use `get_vendors_requiring_escalation()` for the Risk Committee view
   and `evaluate_all_vendors()` for the full picture.

## Common Pitfalls

- **Treating a Proof of Reserves attestation as audit evidence.** The PCAOB Office
  of the Investor Advocate advisory of 2023-03-08 states that PoR engagements *are
  not audits* and that the reports *do not provide any meaningful assurance* —
  typically they say nothing about liabilities or whether assets were borrowed for
  the snapshot. A custodian with a quarterly PoR page and no SOC report has no
  evidence of control effectiveness; this engine rates it `NON_COMPLIANT` /
  `CRITICAL`.
- **Treating a bridge letter as if it were audited coverage.** A bridge letter is
  signed by the *service organisation's management*, not by the service auditor,
  and carries no audit assurance for the period it covers. It closes the unbridged
  window while the next report is prepared; it does not restore the risk rating a
  real report earned, so relying on one caps the vendor at `MEDIUM`. And because
  bridging is a ~3-month device, an 18-month "bridge" over a report that has blown
  the review cadence is not coverage at all — the vendor stays `OVERDUE`.
- **Ignoring Complementary User Entity Controls.** A clean SOC 2 opinion is
  conditional on the *user entity* operating the controls listed in the report's
  CUEC section — dual authorisation on withdrawals, address whitelisting, API key
  scoping, IP restriction, hardware MFA. Unimplemented CUECs void the assurance the
  clean opinion appears to give. Worse, an empty CUEC list usually means nobody
  transcribed the section, not that the custodian requires none — which is why this
  engine reports "not assessed" rather than 100%.
- **Missing the carve-out.** Where the custodian's report uses the **carve-out
  method**, the subservice organisation's controls are excluded from the
  description *and from the scope of the examination*; the report only discloses
  the Complementary Subservice Organisation Controls (CSOCs) it assumes exist,
  untested. If a custodian carves out its cloud host, HSM vendor or MPC provider,
  the SOC report on file evidences nothing about them — obtain that entity's own
  SOC report for an overlapping period.
- **Accepting a Type I report, or a token Type II window.** Type I opines on
  control *design* at a single date. Type II opines on operating effectiveness over
  a period — but the AICPA sets no minimum period, and a 30- or 60-day "Type II"
  is close to a Type I in evidential value. Set `min_type2_coverage_days` to the
  firm's own floor and treat a shorter period as a finding, not a formality.
- **Reading a clean opinion as "no exceptions".** An unqualified opinion routinely
  coexists with exceptions listed in the Section IV test results. Record them in
  `deficiencies_found`; any non-zero count raises the vendor to `HIGH` here.
- **Letting the review cadence drift with the report date.** Staleness is measured
  from the *coverage end date*, never from the date the report was issued or
  received — a report issued in February for a period that ended the previous
  December is already two months old on arrival.

## Verification

Execute the unit test suite. It covers opinion handling, cadence boundaries,
bridge-letter acceptance and every rejection reason, Proof of Reserves freshness,
CUEC scoring including the unassessed and unevidenced cases, ingestion validation,
and the side-effect freedom of evaluation:

```bash
python -m unittest discover -s skills/third-party-custody-audit-report-review-cadence/scripts
```

Repository-wide checks:

```bash
python tools/validate_skills.py
python tools/run_all_tests.py
```

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `custody-solution-vendor-due-diligence-checklist`
- `vendor-lock-in-risk-for-proprietary-custody-formats`
- `air-gapped-signing-workflow-for-cold-storage`
- `exchange-proof-of-reserves-verification`
