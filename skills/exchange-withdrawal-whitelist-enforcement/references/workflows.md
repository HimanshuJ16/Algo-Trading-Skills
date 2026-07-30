# Workflows for Exchange Withdrawal Whitelist Enforcement

1. **API Key Scope Verification**:
   - Confirm API key possesses withdrawal permissions for whitelisted endpoints.
2. **Address Membership Check**:
   - Verify destination address is present in firm whitelisted registry.
3. **Cool-off Period Lock Check**:
   - Verify elapsed time exceeds 24-hour mandatory security lock.
4. **Withdrawal Dispatch**:
   - Approve withdrawal request for execution.