# Workflows for Sandbox Credential Leakage Prevention

1. **Environment Declaration**:
   - Set runtime mode explicitly to SANDBOX or PRODUCTION.
2. **Pre-Request Boundary Validation**:
   - Validate API key prefix and destination URL against environment rules.
3. **Leak Block Enforcement**:
   - Immediately abort outbound request and log security alert if cross-environment mismatch is detected.
