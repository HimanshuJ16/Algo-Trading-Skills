# Workflows for Emergency Manual Override Access Control

1. **Role & Action Verification**:
   - Check operator RBAC role and action severity level.
2. **Dual Sign-Off Audit**:
   - Require secondary sign-off or Break-Glass token for critical kill switches.
3. **Audit Hash Computation**:
   - Compute SHA-256 hash over override payload for tamper-evident logging.
4. **Execution & Expiry**:
   - Execute override and track TTL expiry window.