# Checklist for Annual Compliance Attestation

This checklist mirrors the engine's verdict contract. An agent must not tick these items while the engine blocks (or vice versa).

## Inputs & Entity

- [ ] Confirm `legal_entity_id` — one checklist per legal entity (RIA / BD affiliate / offshore feeder are separate).
- [ ] Confirm `reporting_year` is an int in [2000, 2100] (engine raises `ValueError` otherwise).
- [ ] Confirm every date field is sourced from a tamper-evident, attributable, reproducible audit-log record (SEC 17a-4(f)). REFUSE unprovenanced hand-typed dates.

## SEC Rule 206(4)-7 (RIA — all firms)

- [ ] `annual_policy_review_date` set and within the reporting year.
- [ ] `annual_review_documentation_date` set and within the reporting year.
- [ ] Nine review dimensions documented (who, what, when, how, findings, recommendations, implementation status, documentation, senior-management sign-off).

## Quant-Specific Controls (SEC Exam Priorities)

- [ ] `algo_code_integrity_review_date` set and within the reporting year.
- [ ] `trade_surveillance_test_date` set and within the reporting year.

## BD-Specific Gates (only if `is_broker_dealer = True`)

- [ ] `ceo_cco_meeting_date` within the rolling 12-month window preceding `certification_signing_date` (FINRA 3130(c)(2)).
- [ ] `ceo_cco_meeting_date` no later than the anniversary of `prior_certification_date`.
- [ ] `ceo_cco_meeting_date <= ceo_certification_signed_date` (rubber-stamping guard).
- [ ] `ceo_certification_signed_date` set and within the reporting year.
- [ ] `board_submission_date` within 45 days of `ceo_certification_signed_date` (FINRA 3130.04).
- [ ] `rule_3120_report_date` set and within the rolling 12-month window (FINRA 3120).
- [ ] `rule_15c3_5_annual_review_date` set and within the reporting year (SEC 15c3-5(d)(2)/(e)).
- [ ] `rule_15c3_5_ceo_certification_date` set and within the reporting year.

## Engine Verdict

- [ ] `is_ready_for_attestation == True` with empty `missing_requirements` AND empty `missing_requirement_codes`.
- [ ] A False verdict is a HARD BLOCK — escalate `missing_requirements` to department heads; do NOT auto-remediate or fabricate dates.

## Seal & Archive

- [ ] `AttestationReport.content_hash` logged at INFO on generation.
- [ ] `AnnualComplianceChecklist` and `AttestationReport` are frozen — construct fresh instances for re-runs; do not mutate.
- [ ] CEO/CCO signature follows a True verdict.
- [ ] Archive in 17a-4-compliant storage (WORM OR the 2023 audit-trail alternative per Release 34-96034). Advisers: 5 years (first 2 in-office) per Rule 204-2; BDs: 3-6 years by type per 17a-4.

## Missed Deadline

- [ ] If the anniversary has passed with outstanding gaps: consider FINRA Rule 4530(b) self-reporting within 30 calendar days of discovery; produce a remediation-plan artifact; re-evaluate with a fresh checklist once gaps are genuinely closed.

## Run

- [ ] Run test suite: `python -m unittest discover -s skills/annual-compliance-attestation-workflow/scripts`.

## Sign-off
- Chief Compliance Officer: ___________________________
- Chief Executive Officer: ___________________________
- Date: ___________________________
