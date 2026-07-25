# Workflows for Alternative Data Vendor Due Diligence

## Onboarding Pipeline

1. **Vendor Sourcing**: Quantitative researchers identify a new alternative dataset (e.g., supply chain mapping).
2. **DDQ Issuance**: Compliance/Legal sends the proprietary Due Diligence Questionnaire (DDQ) to the vendor.
3. **Automated Triage**: Vendor responses are fed into `VendorDueDiligenceEvaluator.evaluate()`.
4. **Hard Rejection**: If the engine returns `is_approved = False`, the vendor is immediately disqualified. Researchers are barred from accessing even the trial data.
5. **Manual Legal Review**: If the engine returns `True` but flags `warnings` (e.g., captcha bypassing), the legal team manually reviews the target websites' Terms of Service to clear the risk.
6. **Integration**: Once fully cleared, the data moves to the `alternative-data-feature-integration` pipeline.