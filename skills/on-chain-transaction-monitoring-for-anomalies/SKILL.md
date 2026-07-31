---
name: on-chain-transaction-monitoring-for-anomalies
description: >-
  On-chain transaction anomaly monitor evaluating sanctions/OFAC blacklists, high-value transfer spikes, abnormal gas/priority fee spikes, and unapproved contract interactions for crypto custody security.
domain: Crypto Custody & DeFi Security
subdomain: On-Chain Anomaly Detection & KYT Compliance
tags: ["on-chain-monitoring", "kyt-compliance", "ofac-sanctions", "anomaly-detection", "gas-spikes", "crypto-custody", "defi-security"]
brokers_frameworks: ["EVM Blockchain RPC / Web3", "OFAC SDN List", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when monitoring live or pending mempool on-chain transactions for crypto trading bots, institutional treasuries, or custody wallets. On-chain threats such as wallet key drainer exploits, OFAC-sanctioned address interactions, high-value unauthorized transfers, or abnormal priority gas fee spikes (MEV/front-running) must be detected and blocked instantly. This engine evaluates 4 risk vectors, computes composite risk scores ($0-100$), and triggers automated transaction blocks (`HIGH_RISK_BLOCK`).

## Prerequisites

- On-chain transaction payload (`tx_hash`, `from_address`, `to_address`, `value_usd`, `gas_price_gwei`, `method_signature`, `block_number`).
- Risk policy configuration (`max_transfer_usd`, `max_gas_gwei`, `sanctioned_addresses`, `whitelisted_methods`).

## Workflow

1. **Multi-Vector Risk Audit**:
   - **Vector 1**: Sanctions / Blacklist Check (`sanctioned_addresses` match $\implies +80$ risk points).
   - **Vector 2**: High-Value Transfer Spike (`value_usd > max_transfer_usd` $\implies +40$ risk points).
   - **Vector 3**: Abnormal Gas Price Spike (`gas_price_gwei > max_gas_gwei` $\implies +20$ risk points).
   - **Vector 4**: Unapproved Smart Contract Function (`method_signature` not in `whitelisted_methods` $\implies +30$ risk points).
2. **Composite Risk Classification**:
   - Score $\ge 70 \implies$ `HIGH_RISK_BLOCK` (Freeze & Alert).
   - $30 \le \text{Score} < 70 \implies$ `ANOMALY_SUSPECTED`.
   - Score $< 30 \implies$ `TRANSACTION_SAFE`.
3. **Audit Report Generation**: Output structured `OnChainMonitoringReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static Screening Only at Onboarding**: Failing to continuously monitor active live transactions against updated OFAC SDN lists.
- **Ignoring Gas Price Spikes**: Overlooking sudden gas price surges, which frequently indicate MEV sandwich attacks or key compromise draining.
- **Unrestricted Approval Signatures**: Allowing unapproved contract method calls (e.g. `setApprovalForAll`) to execute without anomaly flags.

## Verification

- Instantiate `OnChainAnomalyMonitorEngine`. Audit standard transfer ($1,000 USD, 30 Gwei, whitelisted method) $\implies$ verify status `TRANSACTION_SAFE` (Score 0). Audit transaction to OFAC sanctioned mixer address $\implies$ verify status `HIGH_RISK_BLOCK` (Score 80+).
- Run `python scripts/test_on_chain_transaction_monitoring_for_anomalies.py`.

## Related Skills

- `withdrawal-velocity-limits-and-anomaly-detection`
- `smart-contract-approval-scope-minimization`
---
