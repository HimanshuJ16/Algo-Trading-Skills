# Workflows for Annual Compliance Attestation

1. **Data Aggregation**: In Q4 of every calendar year, the compliance operations team gathers audit logs from engineering (code integrity reviews) and trading (surveillance reviews).
2. **Execution of Meetings**: For broker-dealers, formalize the FINRA Rule 3130 meeting between the CEO and CCO, keeping minutes.
3. **Automated Triage**: Input the dates of all required meetings and reports into `AnnualComplianceChecklist`.
4. **Evaluation**: Run `AnnualComplianceAttestationEngine.evaluate()`.
5. **Resolution**: If the engine returns `is_ready_for_attestation = False`, escalate the `missing_requirements` to the relevant department heads before year-end.
6. **Signature & Archiving**: Once `is_ready = True`, the CEO/CCO sign the physical/digital certificates and archive them in WORM (Write Once Read Many) storage for SEC/FINRA examiners.