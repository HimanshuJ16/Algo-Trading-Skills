# Workflows for Risk Control Bypass Audit Logging

1. **Bypass Event Capture**:
   - Record timestamp, control name, original/override values, authorizer principal, and justification text.
2. **Severity Classification**:
   - Map bypassed control to CRITICAL/HIGH/MEDIUM severity based on control criticality.
3. **Suspicious Pattern Detection**:
   - Flag unauthorized principals or missing/insufficient justification text.
4. **Audit Report Generation**:
   - Output immutable compliance audit trail with totals, critical counts, and suspicious flags.