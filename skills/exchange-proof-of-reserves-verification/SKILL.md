---
name: exchange-proof-of-reserves-verification
description: >-
  Quantitative crypto custody and audit engine for verifying exchange Merkle Sum Tree balance inclusion proofs, validating on-chain reserve ratios (>= 100%), and detecting solvency deficits.
domain: Crypto Custody & Security
subdomain: Proof of Reserves & Solvency Audit
tags: ["proof-of-reserves", "merkle-sum-tree", "on-chain-audit", "crypto-custody", "solvency-verification", "binance-por", "sha256"]
brokers_frameworks: ["Binance PoR Specification", "Kraken PoR Standard", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative trading, exchange risk assessment, and custodial asset security. Following high-profile exchange collapses, centralized crypto exchanges (Binance, Coinbase, Kraken, OKX) publish cryptographic **Proof of Reserves (PoR)**. This module verifies Merkle Sum Tree user balance inclusion proofs, checks on-chain wallet reserve backing ($\text{Reserve Ratio} \ge 100.0\%$), and detects hidden liabilities or negative balance manipulation in Merkle tree snapshots.

## Prerequisites

- Exchange Merkle Root hash and total declared user liabilities ($L_{\text{merkle}}$ in BTC/ETH/USDT).
- User account balance and Merkle audit path (sibling hashes and balances).
- Verified on-chain wallet addresses and balance signatures ($A_{\text{onchain}}$).

## Workflow

1. **Merkle Inclusion Proof Verification**:
   - Compute Leaf Hash = $\text{SHA256}(\text{account\_id} \mathbin{\Vert} \text{balance})$.
   - Hash along the Merkle audit path to recalculate the root hash $R_{\text{computed}}$.
   - If $R_{\text{computed}} \ne R_{\text{declared}} \implies$ Flag `INVALID_MERKLE_PROOF`.
2. **Negative Balance Manipulation Audit**:
   - Audit all Merkle nodes to ensure no user balance $u_i < 0$.
3. **On-Chain Solvency & Reserve Ratio Calculation**:
   - $\text{Reserve Ratio \%} = \frac{A_{\text{onchain}}}{L_{\text{merkle}}} \times 100\%$.
   - If $\text{Reserve Ratio} < 100.0\% \implies$ Flag `INSOLVENT_RESERVE_DEFICIT`.
   - Else $\implies$ Flag `SOLVENT_FULL_RESERVES`.
4. **Audit Report Generation**: Output structured `ProofOfReservesAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Negative Balances in Merkle Trees**: Failing to verify that no user balances are negative ($u_i < 0$), allowing exchanges to artificially shrink total declared liabilities.
- **Conflating On-Chain Control with Unencumbered Ownership**: Assuming wallet balances prove solvency without auditing whether assets are pledged as collateral in off-chain DeFi loans.
- **Relying on Outdated PoR Snapshots**: Accepting a Merkle proof published months ago rather than requiring fresh periodic or real-time cryptographic attestations.

## Verification

- Instantiate `ExchangeProofOfReservesEngine`. Input Merkle Tree with Total User Liabilities = 10,000 BTC. On-chain verified wallets = 10,500 BTC. Verify engine computes Reserve Ratio = 105.0%, validates user Merkle inclusion path, and outputs `SOLVENT_FULL_RESERVES`. Input on-chain wallets = 9,200 BTC (92.0% ratio). Verify engine flags `INSOLVENT_RESERVE_DEFICIT`.
- Run `python scripts/test_exchange_proof_of_reserves_verification.py`.

## Related Skills

- `exchange-proof-of-reserves-verification`
- `custody-solution-vendor-due-diligence-checklist`
---
