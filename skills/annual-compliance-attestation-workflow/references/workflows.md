# Workflows for Annual Compliance Attestation

1. **Identify the Legal Entity.** Confirm `legal_entity_id`. One `AnnualComplianceChecklist` instance per legal entity (RIA vs. BD affiliate vs. offshore feeder each require their own).
2. **Data Aggregation.** In Q4 of every calendar year, the compliance operations team gathers tamper-evident, source-system-derived audit logs from engineering (code integrity reviews), trading (surveillance reviews), and the CCO office (CEO-CCO meeting minutes, prior certification anniversary, Rule 3120 report, Rule 15c3-5 review/certification).
3. **Execution of Meetings.** For broker-dealers, formalize the FINRA Rule 3130 meeting between the CEO and CCO within the rolling 12-month window preceding the intended signing date, keeping minutes.
4. **Automated Triage.** Input the dates of all required meetings and reports into `AnnualComplianceChecklist` (frozen at construction; construct a fresh instance for re-runs).
5. **Evaluation.** Run `AnnualComplianceAttestationEngine.evaluate()`. The engine returns a sealed `AttestationReport` with `is_ready_for_attestation`, `missing_requirements`, `missing_requirement_codes`, `generated_at`, and `content_hash` (logged at INFO).
6. **Resolution / Escalation.** If the engine returns `is_ready_for_attestation = False`, this is a HARD BLOCK. Escalate `missing_requirements` (and their codes) to the relevant department heads. Do NOT auto-remediate, do NOT fabricate dates, do NOT sign. Re-run the engine only after the underlying gap is genuinely closed with a new tamper-evident record (construct a fresh checklist).
7. **Signature & Archiving.** Once `is_ready_for_attestation = True`, the CEO/CCO sign the physical/digital certificates and archive them in 17a-4-compliant storage via EITHER WORM (Write Once Read Many) storage OR the 2023 SEC audit-trail alternative (Release 34-96034, effective Jan 3, 2023; compliance May 3, 2023): a complete time-stamped audit trail of all modifications/deletions able to recreate originals, with the designated-executive-officer attestation option. Retention: advisers retain annual-review records 5 years (first 2 in-office) per Rule 204-2; BD records 3-6 years by type per 17a-4.
8. **Board / Audit-Committee Distribution (BD).** For broker-dealers, submit the signed certification to the board of directors / audit-committee no later than 45 days after `certification_signing_date` (FINRA Rule 3130.04), or the next scheduled meeting, whichever is earlier. Record `board_submission_date` and `audit_committee_acknowledgment_date`.

## Missed-Attestation Recovery Path

If the certification deadline (anniversary of the prior certification) has passed with outstanding gaps:

1. **Do NOT sign.** A False verdict is a HARD BLOCK.
2. **Consider FINRA Rule 4530(b) self-reporting.** The firm must consider self-reporting the missed deadline within 30 calendar days of discovery, filed via FINRA Gateway. Late filings appear on the firm's 4530 Disclosure Timeliness Report Card.
3. **Produce a remediation-plan artifact.** Document the root cause, the remediation steps, the responsible owner, and the target close date.
4. **Re-evaluate.** Once the underlying gap is genuinely closed with tamper-evident evidence, construct a fresh `AnnualComplianceChecklist` and re-run the engine. Do not mutate the prior sealed checklist.
