# Pre-Flight / Sign-off Checklist — crypto-wallet-key-custody-security

Use this before considering the skill's implementation complete.

## Permission Scoping

- [ ] **Aggregate verdict:** Run `KeyCustodySecurityAuditor` over every key and confirm `summary().passed` is True (no CRITICAL and no HIGH findings).
- [ ] **Real permission names:** Confirm the audit input uses the exchange's actual field names — Binance `enableWithdrawals` / `enableInternalTransfer` / `permitsUniversalTransfer`, Coinbase `can_transfer`, Kraken "Withdraw Funds" — not the literal string `withdraw`.
- [ ] **Attribution recorded:** Confirm every key declares `used_by`; an unattributed key holding a funds-moving permission is CRITICAL, not clean.
- [ ] **Reconciled against the exchange:** Confirm the declared config matches the exchange's own permission endpoint — this module audits what you declare, not what the exchange grants.
- [ ] **Withdrawal keys accounted for:** Confirm every key that legitimately carries a funds-moving permission is human-gated and appears in the audit as a reviewed HIGH finding, not as a pass.

## Network & Storage

- [ ] **IP Whitelisting:** Confirm all active API keys are bound to static trusted IPs, with `ip_whitelisted` set to exactly `True` (not a truthy placeholder).
- [ ] **90-day downgrade:** Confirm awareness that Binance.US resets non-IP-whitelisted keys to read-only after 90 days unused.
- [ ] **Secret Storage:** Confirm every key declares a backend on the secure allowlist (AWS/GCP/Azure KMS, HashiCorp Vault, hardware HSM). Confirm no key relies on an unrecognized or undeclared backend.

## Capital Bounding

- [ ] **Hot Balance Bounding:** Confirm hot balances are within `max_hot_ratio`, and that the ratio in use was **chosen from your own loss tolerance** — the 15% default is a policy default, not an industry standard.
- [ ] **Undeterminable ratios:** Confirm a zero/negative/NaN total balance is reported unsafe rather than reading as "0% hot".
- [ ] **Multi-sig threshold:** Confirm `multisig_threshold` is set (it is disabled by default) and that real enforcement lives in the wallet/custodian policy engine, not only in this module's bookkeeping.

## Transfer Monitoring

- [ ] **Address normalization:** Confirm EIP-55 checksummed EVM addresses match their lowercase whitelist entries, and that Base58 addresses are compared case-sensitively (folding their case would be a fail-open).
- [ ] **Outbound Transfer Monitoring:** Confirm independent monitoring detects and alerts on non-whitelisted withdrawal attempts, verified to work while the bot's primary process is stopped.
- [ ] **Alert channel resilience:** Confirm a failing alert channel does not abort the audit and is itself recorded as a finding.
- [ ] **Exchange-side rejection:** Confirm a test withdrawal to a non-whitelisted address is rejected by the exchange itself, not merely flagged by this module.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/crypto-wallet-key-custody-security/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
