# Deep Workflow Reference — crypto-wallet-key-custody-security

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Permission Scoping Audit:**
   - Audit all API key credentials using `KeyCustodySecurityAuditor`.
   - Never grant `WITHDRAW` permission to API keys used by automated trading processes.
   - Separate keys into read-only market data feeds, trade-only keys, and human-gated withdrawal credentials.

2. **IP Whitelisting & Network Boundary Controls:**
   - Enforce static IP address allowlisting on all exchange API keys.
   - Disallow unrestricted `0.0.0.0/0` IP access on API management panels.

3. **Storage Security Backend Inspection:**
   - Store API secrets and private keys exclusively in encrypted Key Management Systems (AWS KMS, GCP KMS, Azure Key Vault, HashiCorp Vault, or hardware HSMs).
   - Prohibit storing secrets in plaintext `.env` files or committed config files.

4. **Hot vs. Cold Storage Capital Allocation:**
   - Bound operational exchange "hot" balance to a maximum ratio (e.g. $\le 15\%$ of total portfolio value) using `evaluate_hot_cold_allocation()`.
   - Maintain the majority of assets in cold storage (offline multisig hardware wallets or institutional custody solutions).

5. **Independent Outbound Transfer Monitoring:**
   - Configure independent balance and transfer monitoring via `audit_outbound_transfer()`.
   - Enforce pre-approved destination address whitelisting and trigger instant out-of-band alerts (SMS, Telegram, PagerDuty) on unauthorized transfers.

## Failure Modes Observed in Production

- **Combined Trade & Withdraw Key:** Granting `WITHDRAW` permission to trading bot keys for convenience, leading to total fund drain if the bot host is compromised.
- **Unrestricted IP Access:** Using API keys without static IP binding, allowing stolen keys to be used from any server worldwide.
- **Plaintext Secret Exposure:** Hardcoding API secrets in source code or `.env` files exposed via git repositories or log dumps.
- **Overallocated Hot Wallet:** Storing 100% of trading capital on-exchange, exposing full portfolio value to single-point-of-failure exchange or API key compromises.

## Production Implementation Reference

- Reference code: `scripts/key_permission_audit.py` (`KeyCustodySecurityAuditor`, `AuditFinding`, `RiskLevel`, `StorageBackend`).
- Automated unit tests: `scripts/test_key_permission_audit.py`.
