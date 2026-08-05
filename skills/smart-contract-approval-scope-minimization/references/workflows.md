# Workflows for Smart Contract Approval Scope Minimization

1. **Allowance Pre-Check**:
   - Query current on-chain allowance for spender address.
2. **Permit / Exact Amount Selection**:
   - Select EIP-2612 signed permit if supported; otherwise set exact transaction notional.
3. **Approve-to-Zero Reset**:
   - Issue `approve(spender, 0)` if updating an existing non-zero allowance.
4. **Stale Allowance Audit**:
   - Scan wallet allowances periodically and revoke unused approvals.