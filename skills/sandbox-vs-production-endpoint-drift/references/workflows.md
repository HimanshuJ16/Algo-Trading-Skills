# Workflows for Sandbox vs Production Endpoint Drift

1. **Payload Schema Comparison**:
   - Compare JSON field keys and data types across sandbox and prod payloads.
2. **Rate Limit Header Auditing**:
   - Verify rate-limit headers in prod are mirrored in sandbox.
3. **Status Code Verification**:
   - Compare HTTP status codes for identical invalid payloads.
4. **Drift Report Output**:
   - Output structured drift report flagging CRITICAL and WARNING findings.
