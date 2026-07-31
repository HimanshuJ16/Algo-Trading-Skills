# Workflows for Phishing-Resistant Authentication for Custody Access

1. **Origin Binding Verification**:
   - Verify client_origin matches expected Relying Party URL (https://{rp_id}).
2. **User Presence & Verification Check**:
   - Enforce hardware key touch (UP=1) and PIN/biometric verification (UV=1).
3. **Challenge Validation**:
   - Verify challenge token freshness and cryptographic signature.
4. **Audit Report Generation**:
   - Output structured auth verification report.