---
name: exchange-withdrawal-whitelist-enforcement
description: >-
  Quantitative crypto custody and security engine for enforcing exchange withdrawal address allowlists, auditing mandatory 24h/48h cool-off locks, and preventing API key drain attacks.
domain: Crypto Custody & Security
subdomain: Exchange Security & Address Whitelisting
tags: ["withdrawal-whitelist", "address-lockdown", "crypto-custody", "24h-cooloff-lock", "api-security", "binance-whitelisting", "cold-storage"]
brokers_frameworks: ["Binance Whitelist API", "Coinbase Allowlist", "Kraken Security", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative trading infrastructure, hot wallet management, and automated custody withdrawal pipelines. To protect against API key compromise, session hijacking, or malicious insider attacks, crypto exchanges (Binance, Coinbase, Kraken, OKX) enforce **Withdrawal Address Whitelisting** (Allowlisting). Outbound withdrawals are strictly restricted to pre-approved addresses, and newly added addresses are subject to a mandatory **24-hour to 48-hour cool-off security lock**.

## Prerequisites

- Whitelisted address registry (`destination_address`, `asset_symbol`, `added_timestamp_seconds`, `cooloff_duration_seconds`: 86,400s).
- Proposed withdrawal request details (`asset_symbol`, `amount`, `destination_address`, `request_timestamp_seconds`).
- API key permissions (`is_withdrawal_enabled_on_key`: True/False).

## Workflow

1. **API Key Permission Audit**:
   - Check if trading API key has general withdrawal scope. If False $\implies$ Flag `API_KEY_WITHDRAWAL_DISABLED`.
2. **Address Whitelist Membership Audit**:
   - Check if `destination_address` exists in registered whitelisted records for `asset_symbol`.
   - If missing $\implies$ Flag `UNAUTHORIZED_ADDRESS_REJECTION`.
3. **24-Hour Security Cool-off Lock Audit**:
   - Calculate elapsed time $\Delta t = T_{\text{request}} - T_{\text{added}}$.
   - If $\Delta t < \text{cooloff\_duration\_seconds} \implies$ Flag `COOLOFF_PERIOD_ACTIVE_REJECTION` (calculate remaining lock seconds).
   - Else $\implies$ Flag `WITHDRAWAL_APPROVED`.
4. **Audit Report Generation**: Output structured `WithdrawalWhitelistAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing the 24-Hour Cool-off Lock**: Expecting automated API requests to withdraw to a newly added address immediately, causing script failures due to cool-off rejections.
- **Enabling General Withdrawals on Trading API Keys**: Creating API keys with blanket withdrawal permissions rather than restricting withdrawals to whitelisted addresses.
- **Ignoring Network-Specific Address Formats**: Submitting EVM addresses without validating checksums or destination tags (e.g. XRP Memo, EOS Memo).

## Verification

- Instantiate `ExchangeWithdrawalWhitelistEngine`. Register whitelisted address `bc1q_cold_storage_address` added 48 hours ago (cool-off = 24h). Request withdrawal of 2.0 BTC to `bc1q_cold_storage_address`. Verify engine outputs `WITHDRAWAL_APPROVED`. Request withdrawal to unregistered address `bc1q_hacker_address`. Verify engine flags `UNAUTHORIZED_ADDRESS_REJECTION`. Add new address 2 hours ago. Verify engine flags `COOLOFF_PERIOD_ACTIVE_REJECTION`.
- Run `python scripts/test_exchange_withdrawal_whitelist_enforcement.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `exchange-proof-of-reserves-verification`
---
