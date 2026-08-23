---
name: crypto-wallet-key-custody-security
description: Use when a trading bot has any access to crypto private keys, exchange
  API keys with withdrawal permission, or wallet infrastructure, to bound the damage
  a compromised bot or leaked credential can cause
domain: algorithmic-trading
subdomain: crypto-custody-security
tags:
- crypto-custody-security
brokers_frameworks: []
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any crypto trading system where compromise of the bot's credentials could result in irreversible loss of funds — unlike a traditional equities broker (where a compromised API key typically allows unauthorized trades but not direct fund withdrawal to an arbitrary destination, and trades are generally reversible or insurable through the broker/exchange), a compromised crypto exchange API key with withdrawal permission, or a leaked private key, can result in an instant, irreversible transfer of funds to an attacker-controlled address. This skill treats key/credential custody as a distinct, higher-stakes concern from general API-credential handling.

## When NOT to Use

- **As a substitute for exchange-side controls.** `KeyCustodySecurityAuditor` audits a *declared* configuration. It cannot read the exchange's actual key permissions — it audits what you tell it. Reconcile its input against the exchange's own permission endpoint (Binance `GET /sapi/v1/account/apiRestrictions`, Coinbase `GET /api/v3/brokerage/key_permissions`, Kraken's key management page) or you are auditing a document, not a system.
- **As a key store, signer, or custody solution.** This module holds no keys and signs nothing. Key generation, storage and signing belong in KMS/HSM/vault or an institutional custodian.
- **For non-custodial / self-custody wallet operations** where the private key never touches the trading system — the withdrawal-permission model here does not apply; see `air-gapped-signing-workflow-for-cold-storage` and `hardware-security-module-hsm-for-signing-keys`.
- **As the sole approval gate for large transfers.** `evaluate_transfer_approval` records whether required approvals were present; it does not *collect* or cryptographically verify them. Real multi-signature enforcement must live in the wallet/custodian policy engine, outside any path the trading system controls.
- **To decide whether a specific address is safe.** Whitelist membership is not a safety property; it only means someone pre-approved that destination through a separate process.

## Prerequisites

- A clear map of exactly which systems/processes need which permissions (read-only market data, trade-only, or trade-plus-withdrawal) — most trading bot logic never needs withdrawal permission at all
- An exchange or custody solution that supports API key permission scoping and, ideally, withdrawal address whitelisting
- A `used_by` attribution recorded for every key. The auditor treats an unattributed key holding a funds-moving permission as CRITICAL, because it cannot rule out that a bot holds it.

## Workflow

1. **Scope permissions, and enumerate them by the exchange's real names.** Never grant withdrawal permission to a key used by the bot's trading logic; create separate keys scoped to exactly what each process needs. Decision point: do not grep for the literal string `withdraw` — no major exchange uses it. Binance exposes `enableWithdrawals`, **and also** `enableInternalTransfer` and `permitsUniversalTransfer`, which move funds too; Coinbase exposes `can_transfer` ("deposit/withdrawal permissions"); Kraken names it "Withdraw Funds". A check written against one exchange's vocabulary silently passes another's. The auditor matches the stems `withdraw` and `transfer` and is deliberately over-inclusive: a false positive costs a review, a false negative costs the balance.
2. **Enable withdrawal address whitelisting** and restrict withdrawals to pre-approved addresses set up through a separate, higher-friction process (ideally requiring manual human action, not automatable by the bot) — so even a compromised withdrawal-capable key cannot redirect funds to an arbitrary attacker address.
3. **Compare destination addresses by encoding, not by string.** Decision point: an EIP-55 checksummed EVM address and its all-lowercase form are *the same address* — mixed case is a checksum layered on case-insensitive hex, so comparing them verbatim produces false "unapproved" alerts. But Base58Check (legacy BTC) addresses are **case-sensitive**, so case-folding them would let two distinct addresses collide and turn the whitelist into a fail-open. Bech32 is case-insensitive but must never be mixed-case (BIP-173). Normalize per format — never blanket-lowercase.
4. **Split operational balance from total holdings.** Keep only the capital the bot actively needs in hot, bot-reachable balance and hold the majority in cold storage requiring a separate, deliberately higher-friction process to release. This bounds maximum loss from any single compromise to the hot balance. The `max_hot_ratio=0.15` default is a **policy default, not an industry standard** — no regulator or standards body sets this number; choose it from your own loss tolerance.
5. **Store secrets only in backends you can name.** Prefer a dedicated secrets-management solution (HSM, cloud KMS, or a vault service) over plaintext config files or environment variables. Decision point: validate storage against an **allowlist of approved backends**, not a denylist of bad ones — a denylist silently passes every value it does not recognize (`""`, `"dotenv"`, a typo), which is exactly the shape of failure a custody audit must not have.
6. **Require multi-signature approval above a defined threshold**, structurally separate from the bot's own logic — mirroring `kill-switch-and-drawdown-circuit-breakers`, where risk-critical decisions live in a module the strategy cannot unilaterally override. CCSS Level II adds multi-signature controls and Level III requires multiple actors for all critical actions.
7. **Monitor balances and outbound transfers via an independent channel**, not solely the bot's own logging, so an unauthorized transfer is detected within minutes. Decision point: an alert channel that throws must not abort the audit — the monitor would then die on the exact event it exists to report. Treat a failed alert delivery as its own finding.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Cited standards and storage-backend coverage: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing for the literal word "withdraw".** Binance's `enableWithdrawals`, Coinbase's `can_transfer` and Kraken's "Withdraw Funds" all fail an exact `"withdraw"` match, and `enableInternalTransfer` / `permitsUniversalTransfer` move funds without the word appearing at all.
- **Matching the key's owner by exact string.** A check keyed on `used_by == "trading_bot"` passes `"trading-bot"`, `"strategy_engine"`, and — worst — a config that simply omits the field. An unattributed key holding withdrawal permission is the *most* suspicious case, not the safest.
- **Denylisting insecure storage backends instead of allowlisting secure ones**, so an unrecognized or misspelled backend audits clean.
- **Treating an undeterminable hot/cold ratio as safe.** A zero, negative or NaN total balance divides into a ratio of 0.0 and reads as "0% hot" while the hot wallet is full. Incoherent input is a finding, not a pass.
- **Blanket-lowercasing addresses before whitelist comparison**, which is correct for EVM and bech32 but a fail-open for case-sensitive Base58.
- Granting a single API key both trading and withdrawal permissions "for convenience," eliminating the protective boundary that scoped permissions would otherwise provide.
- Not enabling withdrawal address whitelisting where the exchange supports it, leaving a compromised withdrawal-capable key free to redirect funds anywhere.
- Keeping the full portfolio balance in hot, bot-accessible storage rather than splitting into operational hot balance and cold storage for the majority of capital.
- Storing private keys or exchange API secrets in plaintext environment variables or config files committed to a repository (even a private one) rather than in a dedicated secrets-management solution.
- **Leaving keys without IP restrictions.** Beyond the theft risk, Binance.US resets API key permissions to read-only for keys unused for 90 days that are not secured by IP whitelisting — an unrestricted key can silently lose trading permission.
- Relying solely on the bot's own logs to notice unauthorized transfer activity, rather than an independent monitoring/alerting channel that would catch a compromise even if the bot's own logging were also compromised or disabled by the attacker.

## Verification

- Audit every API key and confirm `KeyCustodySecurityAuditor.summary().passed` is True — it is False whenever any CRITICAL or HIGH finding exists. Confirm no trading-logic key carries a funds-moving permission under *any* exchange's naming.
- Feed a key config with `used_by` omitted and a withdrawal permission: it must report CRITICAL, not pass.
- Feed an unrecognized `storage_backend` (`"dotenv"`, `""`, a typo): it must be flagged as insecure, not accepted.
- Feed `hot_balance=50_000, total_balance=0`: it must report unsafe, not "0% hot".
- Confirm an EIP-55 checksummed address matches its lowercase whitelist entry, and that a lowercased Base58 address does **not** match its correctly-cased entry.
- Confirm an alert channel that raises does not abort the audit and produces its own HIGH finding.
- Confirm withdrawal address whitelisting is enabled at the exchange, and that a test withdrawal to a non-whitelisted address is rejected by the exchange itself — not merely by this module.
- Confirm an independent alert fires on an outbound transfer, verified to work even if the bot's primary process is stopped.
- Run `python -m unittest discover -s skills/crypto-wallet-key-custody-security/scripts`.

## Related Skills

- `headless-broker-auth-patterns`
- `kill-switch-and-drawdown-circuit-breakers`
- `crypto-exchange-api-integration`
- `exchange-withdrawal-whitelist-enforcement`
- `multi-signature-approval-for-large-transfers`
- `hot-cold-wallet-split-for-trading-bots`
- `api-key-least-privilege-audit-tool`
