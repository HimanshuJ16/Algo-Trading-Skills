# Deep Workflow Reference — regional-broker-data-residency-constraints

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Broker Residency Policy Setup**:
   - Register target broker policies mapping brokers to legal jurisdictions (`IN`, `EU`, `US`) and allowed AWS/GCP regions.

2. **Probe Hosting Environment Region**:
   - Inspect environment variables (`AWS_REGION`, `GCP_REGION`) or query metadata.

3. **Data Residency Compliance Audit**:
   - Verify active hosting region matches allowed regions for target broker.

4. **Compliance Veto**:
   - If region breaches residency rules, raise `DataResidencyViolationError` and halt bot initialization.

## Production Implementation Reference

- Reference code: `scripts/residency_guard.py` (`DataResidencyComplianceGuard`, `BrokerResidencyPolicy`, `DataResidencyViolationError`).
- Automated unit tests: `scripts/test_residency_guard.py`.
