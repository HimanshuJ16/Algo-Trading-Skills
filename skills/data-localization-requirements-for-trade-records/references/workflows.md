# Workflows for Data Localization Requirements for Trade Records

1. **Policy Inspection**:
   - Determine `origin_jurisdiction` and `record_type`.
2. **Region Validation**:
   - Validate `destination_cloud_region` against allowed national cloud zones.
3. **Egress Interception**:
   - Block cross-border egress for restricted regimes (CN, IN, RU).
4. **Compliance Audit**:
   - Generate compliance report and audit trail.