---
name: cross-chain-address-reuse-privacy-risk
description: Quantitative crypto custody and security module for auditing cross-chain
  address reuse, detecting deanonymization linkages across EVM/UTXO networks, and
  calculating privacy risk scores.
domain: Crypto Custody & Security
subdomain: On-Chain Privacy & Deanonymization
tags:
- crypto-security
- address-reuse
- privacy-risk
- hd-wallet
- bip44
- deanonymization
- chainalysis
brokers_frameworks:
- BIP-44 Standard
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional crypto trading desks and automated bot architectures to audit wallet address reuse across multiple blockchain networks (Ethereum, Arbitrum, Solana, Bitcoin). Using identical public key addresses or static EVM `0x...` addresses across multiple chains enables on-chain analytics firms (Chainalysis, Elliptic, Nansen) to deanonymize proprietary trading strategies, track total fund AUM, and link private wallets to KYC exchange deposits. This module computes an Address Reuse Privacy Risk Score and recommends HD Wallet BIP-44 path isolation.

## Prerequisites

- Active wallet address registry containing `address`, `public_key`, `chain_id`, and `is_kyc_linked` attributes.
- Defined risk score thresholds (`HIGH_RISK` $\ge 70.0$, `MEDIUM_RISK` $\ge 40.0$).

## Workflow

1. **Wallet Address Graph Ingestion**: Ingest wallet records across chains ($C_1, C_2, \dots, C_m$).
2. **Cross-Chain Linkage Detection**:
   - Identify identical addresses or public keys appearing across $K > 1$ chains.
   - Detect if any address in the cluster has been linked to a KYC exchange deposit/withdrawal.
3. **Privacy Risk Score Calculation**:
   - $\text{Reuse Weight} = \frac{K_{\text{reused\_chains}}}{M_{\text{total\_chains}}} \times 50.0$.
   - $\text{KYC Penalty} = 50.0$ if KYC-linked else $0.0$.
   - $\text{Risk Score} = \min(100.0, \text{Reuse Weight} + \text{KYC Penalty})$.
4. **Remediation Directive Generation**:
   - Enforce HD Wallet BIP-44 `coin_type` derivation path separation.
   - Mandate unique address rotation per sub-account.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static 0x EVM Address Deployment**: Deploying the exact same bot address across 8 EVM chains, making all multi-chain trading volume publicly linkable to a single entity.
- **KYC Deposit Contamination**: Depositing funds from a private strategy address directly into a KYC exchange account, deanonymizing the entire cross-chain wallet graph.
- **Ignoring Public Key Extraction**: Assuming different chain address formats (e.g. Bitcoin vs Ethereum) prevent linking, forgetting that spending transactions reveal the underlying secp256k1 public key.

## Verification

- Instantiate `CrossChainAddressPrivacyAuditor`. Register wallet `0x123...abc` active on 5 EVM chains (`Ethereum`, `Arbitrum`, `Optimism`, `Polygon`, `BSC`). Mark 1 address as KYC-linked on Binance. Verify auditor flags a `HIGH_RISK` (Risk Score = 100.0) deanonymization alert and recommends BIP-44 path isolation.
- Run `python scripts/test_cross_chain_address_reuse_privacy_risk.py`.

## Related Skills

- `phishing-resistant-authentication-for-custody-access`
- `on-chain-transaction-monitoring-for-anomalies`
---
