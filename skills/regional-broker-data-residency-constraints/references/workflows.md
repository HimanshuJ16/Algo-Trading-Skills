# Workflows for Regional Broker Data Residency Constraints

1. **Region Probe**:
   - Detect active cloud provider and region from environment variables (AWS_REGION, GCP_REGION).
2. **Policy Lookup**:
   - Map broker name to jurisdictional residency policy with allowed region sets.
3. **Compliance Validation**:
   - Check current region against broker's allowed AWS/GCP regions.
4. **Violation Handling**:
   - Raise DataResidencyViolationError with regulatory citation if non-compliant.
