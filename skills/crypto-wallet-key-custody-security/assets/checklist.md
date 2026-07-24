# Pre-Flight / Sign-off Checklist — crypto-wallet-key-custody-security

Use this before considering the skill's implementation complete.

- [ ] **Permission Scoping Audit:** Run `KeyCustodySecurityAuditor` and confirm zero trading bot keys possess `WITHDRAW` permission.
- [ ] **IP Whitelisting Verification:** Confirm all active API keys are bound to static trusted IP addresses on exchange management panels.
- [ ] **Secret Storage Inspection:** Confirm API secrets are stored in KMS/Vault services rather than plaintext files or environment variables.
- [ ] **Hot Balance Bounding:** Confirm hot wallet operational balances are bounded below the maximum target ratio ($\le 15\%$).
- [ ] **Outbound Transfer Monitoring:** Confirm independent transfer monitoring detects and alerts on non-whitelisted withdrawal attempts.
- [ ] **Automated Testing:** Run `python scripts/test_key_permission_audit.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
