# Deep Workflow Reference — crypto-wallet-key-custody-security

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

**Controlling design rule:** every check in this skill fails CLOSED. An unrecognized
storage backend, an unattributed key, an unparseable balance or an unknown permission
name yields a finding, never a silent pass. An auditor that returns "clean" because it
did not understand its input manufactures false assurance about irreversible losses.

## Full Procedure

1. **Permission Scoping Audit:**
   - Audit all API key credentials using `KeyCustodySecurityAuditor.audit_key_config()`,
     then read the aggregate verdict from `summary()`. `summary().passed` is False
     whenever any CRITICAL or HIGH finding exists.
   - Enumerate permissions using the exchange's real field names, not the word
     "withdraw" (see `references/standards.md` for the cited vocabulary):
     Binance `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer`;
     Coinbase `can_transfer`; Kraken "Withdraw Funds". The auditor matches the stems
     `withdraw` and `transfer` and is deliberately over-inclusive.
   - Record `used_by` for every key. A funds-moving permission on a key attributed to
     an automated process is CRITICAL; on an unattributed key it is *also* CRITICAL,
     because the auditor cannot rule out that a bot holds it. On a correctly attributed
     human-gated key it is HIGH — a withdrawal key never audits clean.
   - Reconcile the audited config against the exchange's own permission endpoint. This
     module audits what you declare, not what the exchange actually grants.

2. **IP Whitelisting & Network Boundary Controls:**
   - Enforce static IP allowlisting on all exchange API keys; disallow `0.0.0.0/0`.
   - `ip_whitelisted` must be exactly `True`. Truthy-but-unverified values (`"yes"`, `1`)
     are not evidence of an allowlist and are reported.
   - Binance.US resets keys to read-only after 90 days unused when not IP-whitelisted,
     so an unrestricted key can silently lose trading permission as well as being exposed.

3. **Storage Security Backend Inspection:**
   - Validate against the **allowlist** `SECURE_BACKENDS` (AWS/GCP/Azure KMS, HashiCorp
     Vault, hardware HSM). A denylist of insecure backends passes everything it does not
     recognize — `""`, `"dotenv"`, `".env"`, a typo — which is the precise failure mode
     this control exists to prevent.
   - An undeclared backend is reported separately from a recognized-insecure one, so the
     remediation ("declare it") differs from ("migrate it").

4. **Hot vs. Cold Storage Capital Allocation:**
   - Bound the operational hot balance with `evaluate_hot_cold_allocation()`.
   - The `max_hot_ratio = 0.15` default is a **policy default, not a standard**. No
     regulator or standards body sets this number; calibrate it to your loss tolerance.
   - The threshold is inclusive: a ratio exactly equal to `max_hot_ratio` is safe.
   - Non-finite or negative balances raise. A zero total with a non-zero hot balance is
     reported unsafe with an infinite ratio — it is incoherent input, not 0% hot.

5. **Multi-Signature Approval Threshold:**
   - `evaluate_transfer_approval(amount, approvals_present)` blocks transfers at or above
     `multisig_threshold` (inclusive boundary) carrying fewer than `required_approvals`.
   - Disabled by default (`multisig_threshold=None`); set it from your mandate.
   - This *records* whether approvals were present. It does not collect or cryptographically
     verify them — real enforcement belongs in the wallet/custodian policy engine, outside
     any path the trading system controls. CCSS Level II adds multi-signature controls;
     Level III requires multiple actors for all critical actions.

6. **Independent Outbound Transfer Monitoring:**
   - Configure independent transfer monitoring via `audit_outbound_transfer()`, with
     out-of-band alerts (SMS, Telegram, PagerDuty).
   - Addresses are normalized per encoding before comparison (see `normalize_address`):
     EVM case-insensitively (EIP-55 mixed case is a checksum, not a distinct address),
     bech32 folded to lowercase with mixed case rejected (BIP-173), and Base58Check
     compared exactly because its alphabet is case-sensitive.
   - Amounts must be positive and finite; an unparseable destination is treated as
     UNAPPROVED, and an unparseable whitelist entry is recorded as its own finding rather
     than silently shrinking the approved set.
   - A failing alert channel is caught and recorded as a HIGH finding. The monitor must
     not die on the exact event it exists to report.

## Known Failure Modes

- **Combined Trade & Withdraw Key:** Granting withdrawal permission to trading bot keys for convenience, leading to total fund drain if the bot host is compromised.
- **Vocabulary Mismatch:** An audit grepping for `"withdraw"` passes a Binance key holding `enableInternalTransfer`, or a Coinbase key holding `can_transfer` — both of which move funds.
- **Unattributed Key:** A key config omitting `used_by` slipping through a check keyed on an exact owner string, so the least-accounted-for credential is the one that escapes review.
- **Unrestricted IP Access:** Using API keys without static IP binding, allowing stolen keys to be used from any server worldwide.
- **Plaintext Secret Exposure:** Hardcoding API secrets in source code or `.env` files exposed via git repositories or log dumps.
- **Overallocated Hot Wallet:** Storing 100% of trading capital on-exchange, exposing full portfolio value to single-point-of-failure exchange or API key compromises.
- **Checksum False Positives:** Comparing an EIP-55 checksummed destination against a lowercase whitelist entry verbatim, producing "unapproved" alerts for legitimate transfers until operators learn to ignore the alarm.

## Production Implementation Reference

- Reference code: `scripts/key_permission_audit.py` (`KeyCustodySecurityAuditor`, `AuditFinding`, `AuditSummary`, `RiskLevel`, `StorageBackend`, `normalize_address`, `normalize_permission`, `is_funds_moving_permission`).
- Automated unit tests: `scripts/test_key_permission_audit.py`
  (`python -m unittest discover -s skills/crypto-wallet-key-custody-security/scripts`).
