# Checklist for Annual Compliance Attestation

This checklist mirrors the engine's verdict contract. An agent must not tick these items while
the engine blocks (or vice versa).

## Inputs & Entity

- [ ] Confirm `legal_entity_id` — one checklist per legal entity (RIA / BD affiliate / offshore feeder are separate).
- [ ] Confirm `reporting_year` is an int in [2000, 2100] (engine raises `ValueError` otherwise).
- [ ] Confirm `is_broker_dealer` and `has_market_access` are `bool`. Set `has_market_access=False` for a BD that is not an addressee of SEC Rule 15c3-5(b).
- [ ] Confirm every date field is a `datetime.datetime` (not a `date`, not a string) and that all supplied datetimes are consistently naive or consistently tz-aware.
- [ ] Confirm every date is sourced from a tamper-evident, attributable, reproducible audit-log record (SEC Rule 17a-4(f)). REFUSE unprovenanced hand-typed dates.
- [ ] Confirm `prior_certification_date` genuinely precedes this cycle's certification date — a transposed pair is rejected at construction with `ValueError`.

## SEC Rule 206(4)-7(b) and the 204-2 record (all registered advisers)

- [ ] `annual_policy_review_date` set and within the reporting year — the Rule 206(4)-7(b) review of the adequacy of the policies and the effectiveness of their implementation.
- [ ] `annual_review_documentation_date` set and within the reporting year — the record of that review, preserved under Rule 204-2(a)(17)(ii). (Note: the 2023 amendment that would have required the review to be *documented in writing* under 206(4)-7(b) was vacated in June 2024; this gate is a books-and-records and exam-readiness control, not a 206(4)-7 mandate.)
- [ ] Nine review dimensions documented (who, what, when, how, findings, recommendations, implementation status, documentation, senior-management sign-off) — the engine does not check these.

## Quant-Specific Controls (SEC/FINRA exam expectations)

- [ ] `algo_code_integrity_review_date` set and within the reporting year.
- [ ] `trade_surveillance_test_date` set and within the reporting year.

## BD-Specific Gates (only if `is_broker_dealer = True`)

- [ ] `certification_signing_date` set to the date of **execution** of the certification — it anchors every rolling window. (Absent it, the engine falls back to `ceo_certification_signed_date`, then to an explicit `as_of=`; with none of the three it blocks rather than using the wall clock.)
- [ ] `ceo_cco_meeting_date` inside the 12 calendar months preceding the execution date (FINRA Rule 3130(c)(2)). The boundary is inclusive.
- [ ] Certification executed **no later than the anniversary of `prior_certification_date`** (FINRA Rule 3130(b), footnote 1). This constrains the certification, not the meeting.
- [ ] `ceo_cco_meeting_date <= ceo_certification_signed_date` (rubber-stamping guard).
- [ ] `ceo_certification_signed_date` set and within the reporting year.
- [ ] `board_submission_date` no later than 45 days after execution (FINRA Rule 3130(c)(3)). Submission before execution is permitted. **Separately diary the next scheduled board/audit-committee meeting** — the rule's deadline is the *earlier* of the two and the engine checks only the 45-day limb. A member with no board or audit committee should read FINRA Rule 3130.09 and record the equivalent body's submission date; the engine requires this field for every BD and would otherwise block falsely.
- [ ] `rule_3120_report_date` inside the 12 months preceding execution (FINRA Rule 3120(a) requires "no less than annually"; the tie to the 3130 cycle is this engine's tightening).
- [ ] If `has_market_access`: `rule_15c3_5_annual_review_date` set and within the reporting year (SEC Rule 15c3-5(e)(1)).
- [ ] If `has_market_access`: `rule_15c3_5_ceo_certification_date` set and within the reporting year (SEC Rule 15c3-5(e)(2)) — a separate act from the (e)(1) review.

## Engine Verdict

- [ ] `is_ready_for_attestation == True` with empty `missing_requirements` AND empty `missing_requirement_codes`.
- [ ] A False verdict is a HARD BLOCK — escalate `missing_requirements` to department heads; do NOT auto-remediate or fabricate dates.
- [ ] Escalation routed on `missing_requirement_codes`, not prose. `REQ_FINRA_3130_CEO_CCO_MEETING` (schedule the meeting) and `REQ_FINRA_3130_CERT_ANNIVERSARY` (the certification deadline itself has passed) are different remediations.

## Seal & Archive

- [ ] `AttestationReport.content_hash` logged at INFO on generation.
- [ ] `AnnualComplianceAttestationEngine.verify_report(checklist, report)` returns True before the report is relied on. The frozen dataclass still holds mutable lists; the seal is what makes tampering detectable.
- [ ] `AnnualComplianceChecklist` and `AttestationReport` are frozen — construct fresh instances for re-runs; do not mutate.
- [ ] CEO/CCO signature follows a True verdict.
- [ ] Archive in 17a-4-compliant storage (WORM OR the 2023 audit-trail alternative per Release No. 34-96034). Advisers: 5 years, first 2 in an appropriate office, per Rule 204-2(e)(1); BDs: 3–6 years by type per 17a-4.

## Missed Deadline

- [ ] If the anniversary has passed: do NOT back-date. Assess FINRA Rule 4530(b) reporting with counsel against the Rule 4530.01 threshold (30 calendar days after concluding a violation occurred); produce a remediation-plan artifact; re-evaluate with a fresh checklist once the gaps are genuinely closed.

## Run

- [ ] Run test suite: `python -m unittest discover -s skills/annual-compliance-attestation-workflow/scripts`.

## Sign-off
- Chief Compliance Officer: ___________________________
- Chief Executive Officer: ___________________________
- Date: ___________________________
