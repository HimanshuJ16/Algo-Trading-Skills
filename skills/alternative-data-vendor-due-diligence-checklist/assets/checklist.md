# Checklist for Alt Data Due Diligence

## DDQ intake
- [ ] DDQ is dated (`ddq_as_of_date` present, not in the future, and within the evaluator's `max_ddq_age_days` window). Undated, stale and future-dated all fail closed.
- [ ] Every DDQ boolean is a strict `bool` (no truthy strings like `'no'` slipping through).
- [ ] Cross-field consistency holds (no `bypasses_captchas`/`scrapes_behind_login` without `is_web_scraped`; no `has_documented_login_authorization` without `scrapes_behind_login`).
- [ ] Map vendor free-form answers to the booleans per the DDQ response-mapping guidance, especially for `is_material_non_public_information` and `has_robust_anonymization`.

## Compliance evaluation
- [ ] Validate vendor responses regarding web scraping methodologies, separating public scraping (ToS/contract question) from behind-login collection (CFAA question).
- [ ] Apply the **provenance** MNPI rubric, not a predictive-power test: the flag asks whether the dataset's origin implies a breached duty of trust or confidence, not whether the signal is good. See `references/standards.md`.
- [ ] Verify PII anonymization procedures meet GDPR/CCPA standards (robust-anonymization rubric, not self-attestation).
- [ ] Confirm Terms-of-Service compliance for any scraped source.
- [ ] Confirm resell rights and that the license scope covers the firm's intended usage.

## Evidence verification (independent of vendor self-attestation)
- [ ] Right-to-audit exercised for the attested booleans.
- [ ] Sample-data inspection performed (or third-party attestation obtained) for anonymization and MNPI claims.
- [ ] The data license itself (not just the DDQ representation) is on file and reviewed for usage scope, redistribution, derived-data, audit-rights, and point-in-time boundaries.
- [ ] For any behind-login collection, the **authorization instrument from the source operator** is on file and read before `has_documented_login_authorization` is set. An authorized login scrape is a warning requiring recorded legal review, never a clean approval.

## Audit trail
- [ ] `DiligenceRecord` persisted to the system of record (DDQ inputs, `decision`, flag/warning codes, reviewer, CCO sign-off, `evaluated_at`, `rule_version`).
- [ ] For `APPROVED_WITH_WARNINGS`, a recorded manual legal review produced a terminating decision, captured in `audit_notes`.
- [ ] For an approved vendor, `next_review_date` is set and added to the re-diligence calendar.
- [ ] Re-diligence is triggered on a reported methodology change.

## Re-diligence triggers (ongoing monitoring)
- [ ] `next_review_date` passing triggers re-issuance of the DDQ.
- [ ] Vendor methodology change triggers re-issuance and blocks downstream data until a fresh `APPROVED` record is produced.
- [ ] Red flags (enforcement action, sanctions match, ownership change, lapsed SOC2/ISO, new subprocessors/jurisdictions, privacy-policy or ToS/robots.txt drift) force-expire the prior record and block downstream data.

## Test suite
- [ ] Run test suite: `python -m unittest discover -s skills/alternative-data-vendor-due-diligence-checklist/scripts`.

## Sign-off
- Chief Compliance Officer: ___________________________
- Date: ___________________________
