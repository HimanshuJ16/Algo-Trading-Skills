# Workflows for Annual Compliance Attestation

1. **Identify the legal entity.** Confirm `legal_entity_id`. One `AnnualComplianceChecklist` per legal entity (RIA, BD affiliate and offshore feeder each require their own). For a BD, also settle `has_market_access`: SEC Rule 15c3-5(b) binds a broker-dealer *with market access*, and a BD without it must not be gated on 15c3-5(e).
2. **Data aggregation.** In Q4 of every calendar year the compliance operations team gathers tamper-evident, source-system-derived audit logs from engineering (code integrity reviews), trading (surveillance testing), and the CCO office (CEO-CCO meeting minutes, prior certification date, Rule 3120 report, Rule 15c3-5(e)(1)/(e)(2) dates). Each date must arrive with a source-system record ID; refuse hand-typed dates.
3. **Fix this year's deadline.** The prior certification date determines it: FINRA Rule 3130(b) footnote 1 requires each ensuing certification to be effected **no later than the anniversary of the previous year's certification**. Work backwards from that date, not from 31 December.
4. **Execute the meetings.** For broker-dealers, hold the FINRA Rule 3130(c)(2) CEO-CCO meeting inside the 12 calendar months preceding the intended execution date, and keep minutes. The meeting must precede the signature, not follow it.
5. **Automated triage.** Load the dates into `AnnualComplianceChecklist` (frozen at construction; construct a fresh instance for re-runs). All dates must be `datetime` objects with consistent timezone awareness — mixing naive and aware values raises `ValueError` at construction rather than failing mid-evaluation.
6. **Evaluation.** Run `AnnualComplianceAttestationEngine().evaluate(checklist)`. Every rolling window is anchored on `certification_signing_date` (the date of execution), falling back to `ceo_certification_signed_date` and then to an explicit `as_of=` argument. The engine never reads the wall clock: with no anchor it blocks the window checks instead of guessing, so the verdict stays reproducible at examination time.
7. **Resolution / escalation.** `is_ready_for_attestation = False` is a HARD BLOCK. Escalate `missing_requirements` and their codes to the relevant department heads. Route on the code, not the prose — `REQ_FINRA_3130_CEO_CCO_MEETING` (schedule a meeting) is a different remediation from `REQ_FINRA_3130_CERT_ANNIVERSARY` (the certification deadline itself has passed; go to step 10). Do NOT auto-remediate, do NOT fabricate dates, do NOT sign. Re-run only after the underlying gap is genuinely closed with a new tamper-evident record, using a fresh checklist.
8. **Signature and archiving.** Once `is_ready_for_attestation = True`, the CEO/CCO sign and archive in 17a-4-compliant storage via EITHER WORM storage OR the 2023 audit-trail alternative (Release No. 34-96034; effective 3 January 2023, Rule 17a-4 compliance date 3 May 2023): a complete time-stamped audit trail of modifications and deletions able to recreate originals. Retention: advisers keep annual-review records 5 years, the first 2 in an appropriate office, per Rule 204-2(e)(1); BD records 3–6 years by type per 17a-4.
9. **Board / audit-committee distribution (BD).** FINRA Rule 3130(c)(3) requires the **final report evidencing the processes** (not the certificate itself) to reach the board of directors and audit committee at the **earlier** of their next scheduled meetings or 45 days after the date of execution. Record `board_submission_date` and `audit_committee_acknowledgment_date`. The engine checks only the 45-day limb — if the next scheduled meeting falls sooner, that is the operative deadline and you must diary it yourself. Submission *before* execution is expressly permitted by the rule text and is not flagged.
10. **Re-verify archived reports.** Before relying on a sealed report in an examination, run `AnnualComplianceAttestationEngine.verify_report(checklist, report)`. It recomputes the SHA-256 seal over the full evidence set, the verdict, both findings lists and `generated_at`, and returns False if any of them has been altered since issue. A frozen dataclass still holds mutable lists; the seal, not the dataclass, is what makes tampering detectable.

## Missed-Attestation Recovery Path

If the certification deadline — the anniversary of the prior certification — has passed, or the
engine returns `REQ_FINRA_3130_CERT_ANNIVERSARY`:

1. **Do NOT sign a back-dated certification.** A False verdict is a HARD BLOCK, and fabricating
   an execution date is a far worse problem than a late one.
2. **Assess FINRA Rule 4530(b) reporting with counsel.** The rule requires a report to FINRA
   within 30 calendar days after the firm "has concluded or reasonably should have concluded"
   that it violated an applicable rule. Rule 4530.01 then limits firm self-reports to conduct
   with widespread or potentially widespread impact, or arising from a material failure of the
   firm's systems, policies or practices. Whether a specific missed certification clears that
   threshold is a legal judgment — the engine does not make it. Late filings appear on the
   firm's 4530 Disclosure Timeliness Report Card.
3. **Produce a remediation-plan artifact.** Document the root cause, the remediation steps, the
   responsible owner and the target close date.
4. **Certify as soon as the gaps are genuinely closed.** A firm may also reset its anniversary
   by certifying before the one-year anniversary of its most recent certification, which
   requires certifying more than once inside a one-year period.
5. **Re-evaluate.** Construct a fresh `AnnualComplianceChecklist` from the new tamper-evident
   records and re-run the engine. Do not mutate the prior sealed checklist.
