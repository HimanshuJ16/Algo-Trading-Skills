---
name: hot-cold-wallet-split-for-trading-bots
description: >-
  Crypto treasury architecture engine for auditing Hot vs Cold wallet balance ratios (15% Hot / 85% Cold), managing automated sweep/refill transfers, and enforcing zero-withdrawal API keys.
domain: Crypto Custody & Security
subdomain: Wallet Allocation & Treasury Management
tags: ["crypto-custody", "hot-wallet", "cold-storage", "treasury-management", "rebalance-sweep", "api-key-security", "circuit-breaker"]
brokers_frameworks: ["Fireblocks", "BitGo", "Coinbase Custody", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative strategies, exchange trading bots, and institutional digital asset treasuries. Keeping 100% of trading capital in exchange hot wallets or online keys exposes funds to counterparty collapse (e.g. FTX), hot wallet hacks, or API key thefts. This module enforces a strict **Hot/Cold Capital Allocation Model** (e.g. 15% Hot / 85% Cold Vault), automatically triggering sweep transfers to Cold Storage when Hot balances exceed 25% or requesting refills when Hot balances drop below 5%.

## Prerequisites

- Current balances (`hot_balance_usd`, `cold_balance_usd`, `warm_buffer_usd`).
- Target ratio configuration (`target_hot_ratio = 0.15`, `max_hot_ratio = 0.25`, `min_hot_ratio = 0.05`).
- API key security parameters (`withdraw_permission_enabled = False`).

## Workflow

1. **Balance & Ratio Audit**:
   - $\text{Total Value} = \text{Hot} + \text{Cold} + \text{Warm}$.
   - $\text{Hot Ratio} = \frac{\text{Hot Balance}}{\text{Total Value}}$.
2. **API Key Security Check**:
   - Verify `withdraw_permission_enabled == False`. If `True`, trigger emergency security alert.
3. **Rebalance Action Evaluation**:
   - If $\text{Hot Ratio} > 0.25 \implies$ Action `SWEEP_TO_COLD` (Transfer excess to Cold Vault).
   - If $\text{Hot Ratio} < 0.05 \implies$ Action `REFILL_HOT_FROM_COLD` (Request Multisig refill).
   - Else $\implies$ Action `HOLD_BALANCES`.
4. **Audit Report Generation**: Output structured `HotColdWalletAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Enabling Withdrawal Permissions on Trading API Keys**: Assigning withdrawal rights to trading bots, enabling hackers to drain funds if API keys are leaked.
- **Keeping 100% Capital Hot**: Storing all fund capital in online exchange accounts rather than sweeping profits to air-gapped Cold Storage.
- **Ignoring Counterparty Exposure Limits**: Failing to cap max hot balance per exchange venue.

## Verification

- Instantiate `HotColdWalletManagerEngine`. Input Hot Balance $=\$300,000$, Cold $=\$700,000$ (Total $\$1\text{M}$, Hot Ratio $= 30\% > 25\%$) $\implies$ verify engine generates `SWEEP_TO_COLD` proposal for $\$150,000$. Input Hot Balance $=\$30,000$ ($3\% < 5\%$) $\implies$ verify engine generates `REFILL_HOT_FROM_COLD` proposal for $\$120,000$.
- Run `python scripts/test_hot_cold_wallet_split_for_trading_bots.py`.

## Related Skills

- `hardware-security-module-hsm-for-signing-keys`
- `exchange-withdrawal-whitelist-enforcement`
---
