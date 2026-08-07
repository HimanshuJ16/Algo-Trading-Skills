---
name: vendor-lock-in-risk-for-proprietary-custody-formats
description: "Institutional risk management skill for evaluating crypto custody vendor lock-in risks, key format portability (BIP-39, SLIP-0039, BIP-32, WIF vs proprietary MPC/HSM blobs), open-source recovery tool availability, migration cost/friction estimation, and disaster recovery self-sovereignty."
domain: Crypto Custody & Infrastructure Security
subdomain: Vendor Risk Management & Key Portability
tags:
- crypto-custody
- vendor-lock-in
- key-portability
- bip-39
- slip-0039
- mpc-key-shares
- disaster-recovery
- self-sovereignty
brokers_frameworks:
- fireblocks
- bitgo
- anchorage
- copper
- safe-gnosis
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when onboarding institutional crypto custodians (e.g. Fireblocks, BitGo, Anchorage, Copper), reviewing key recovery SLAs, or planning multi-custodian migration strategies.

This skill provides institutional mechanisms to:
- Audit key format standards (`BIP39_MNEMONIC`, `SLIP39_SHAMIR`, `BIP32_HD_PATH`, `WIF_PRIVATE_KEY` vs `PROPRIETARY_MPC_SHARE`, `PROPRIETARY_HSM_BLOB`).
- Compute the **Open Standard Compliance Ratio ($R_{\text{open}}$)** and **Portability Score (0 - 100)**.
- Simulate **Disaster Recovery Drills** assessing asset recovery capabilities during vendor outages or insolvency.
- Calculate **Migration Costs & Timelines** factoring in vendor export fees, wallet counts, and multi-chain gas costs.
- Classify custodian **Lock-In Risk Levels** (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).

## Prerequisites

- Python 3.9+
- Custodian architecture documentation and SLA key export provisions.
- Portfolio inventory details (wallet counts, supported blockchain networks).

## Workflow

1. **Construct Custodian Profile**: Instantiate `CustodyProviderProfile` specifying architecture (`MULTISIG_ON_CHAIN`, `MPC_THRESHOLD`, `PROPRIETARY_VAULT`), supported key formats, open-source recovery tool availability, and vendor export fee structures.
2. **Define Portfolio Scope**: Construct `AssetPortfolio` detailing total value, wallet counts, blockchain networks, and average gas fees.
3. **Execute Lock-In Risk Assessment**: Call `evaluate_custody_provider(provider, portfolio)` to compute portability scores, open standard ratios, and lock-in risk levels.
4. **Simulate Disaster Recovery Drill**: Invoke `simulate_disaster_recovery_drill(provider, is_vendor_responsive=False)` to verify self-sovereign key reconstruction without vendor API availability.
5. **Formulate Vendor Contract Remediation**: Apply recommendations (mandating offline open-source recovery tools or cold key backup shares) prior to final SLA execution.

## Common Pitfalls

- **Confusing Online Portals with Key Sovereignty**: Being able to export CSV transactions or initiate web withdrawals does NOT constitute key sovereignty. If the vendor shuts down its API, assets are locked unless raw BIP-39/SLIP-0039 shares are extractable offline.
- **Ignoring Proprietary MPC Key Share Formats**: Many MPC custodians distribute 2-of-3 key shares using proprietary mathematical encodings that CANNOT be combined without the vendor's closed-source software binary.
- **Failing to Execute Recovery Drills**: Accepting vendor key backup promises without executing a quarterly offline key reconstruction drill leads to discovery of un-usable backups during emergencies.
- **Underestimating Multi-Chain Exit Gas Costs**: Migrating thousands of wallets across 10+ EVM/UTXO networks incurs substantial on-chain gas costs and block delay friction.

## Verification

Run the unit test suite to validate low-risk open standard evaluations, high-risk proprietary MPC detection, migration cost calculations, and disaster recovery drill simulations:

```bash
python -m unittest discover -s skills/vendor-lock-in-risk-for-proprietary-custody-formats/scripts
```

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `third-party-custody-audit-report-review-cadence`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `test-transaction-verification-before-large-transfers`

