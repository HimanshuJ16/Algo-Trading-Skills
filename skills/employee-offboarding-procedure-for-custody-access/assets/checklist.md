# Pre-Flight Checklist

- [ ] Is `termination_time_epoch` the end of the last authorised session, not the HR filing time?
- [ ] Was the access inventory built from systems of record rather than from the departing individual?
- [ ] Are IdP/VPN accounts deactivated **and** live sessions and refresh tokens revoked?
- [ ] Are all exchange API keys deleted, including those held in bot configs, CI secrets and shared vaults?
- [ ] Is custody portal access — users, roles, approval quorum, whitelist rights — removed?
- [ ] For a key holder: has the signer set changed **and** the platform confirmed in writing that old shares are invalidated?
- [ ] If an on-chain re-key changed the deposit address, have counterparties been given new instructions?
- [ ] Does the surviving M-of-N quorum still work, and does it still require more than one person?
- [ ] Are hardware tokens collected and sanitised, with the method recorded?
- [ ] Could a seed or recovery phrase have been copied off-device — and if so, have assets moved to fresh material?
- [ ] Does every `not_applicable_steps` waiver carry a written justification?
- [ ] Is the resulting `CustodyOffboardingAuditReport` retained as audit evidence, and is the record at `LOW_RISK`?
