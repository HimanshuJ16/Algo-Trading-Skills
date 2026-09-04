---
name: vendor-lock-in-risk-for-proprietary-custody-formats
description: >-
  Use when onboarding a custodian or planning a migration, scoring key format
  portability across BIP-39, SLIP-0039 and BIP-32 against proprietary MPC or HSM blobs,
  and estimating the exit cost.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: crypto-custody, vendor-lock-in, key-portability, bip-39, slip-0039, mpc-key-shares, disaster-recovery, self-sovereignty
  brokers_frameworks: "fireblocks; bitgo; anchorage; copper; safe-gnosis"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when onboarding institutional crypto custodians (e.g. Fireblocks, BitGo, Anchorage, Copper), reviewing key recovery SLAs, or planning multi-custodian migration strategies. It turns a documented custodian profile into an auditable portability score, a lock-in risk level, and an exit cost estimate, so a custody decision leaves an evidence trail rather than a gut call.

This skill provides institutional mechanisms to:
- Audit declared key export formats (`BIP39_MNEMONIC`, `SLIP39_SHAMIR`, `WIF_PRIVATE_KEY` vs `PROPRIETARY_MPC_SHARE`, `PROPRIETARY_HSM_BLOB`) and distinguish them from derivation metadata (`BIP32_HD_PATH`), which carries no secret.
- Compute the **Open Standard Compliance Ratio** (a coverage diagnostic) and the **Portability Score (0 - 100)**, which is driven by the *best* export path available rather than the average.
- Model **Disaster Recovery Drills** for a vendor-outage or insolvency scenario, including the case where key material exists but is only reachable through a dead vendor API.
- Estimate **Migration Costs & Timelines** from vendor export fees, wallet counts, and multi-chain gas costs.
- Classify custodian **Lock-In Risk Levels** (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).

## When NOT to Use

- **As evidence that keys are recoverable.** `simulate_disaster_recovery_drill` is a desk exercise over a *declared* profile. Only an executed offline drill that reconstructs real key material with the vendor uninvolved demonstrates self-sovereignty.
- **As a legal or regulatory determination.** Custody obligations differ by jurisdiction (EU MiCA Art. 75, NYDFS, the Advisers Act custody rule) and none of them are decided by a score. See `references/standards.md`.
- **For custodian creditworthiness, insurance, or control-environment due diligence.** This skill scores *key portability* only. Use `custody-solution-vendor-due-diligence-checklist` for charter status, SOC 2 scope, insurance and bankruptcy remoteness.
- **For in-house self-custody designs**, where there is no vendor to be locked into. See `air-gapped-signing-workflow-for-cold-storage` and `shamir-secret-sharing-for-key-backup`.

## Prerequisites

- Python 3.10+ (the module uses `from __future__ import annotations`; no third-party dependencies).
- The executed custody agreement and the vendor's recovery documentation — not the sales summary. Every boolean on `CustodyProviderProfile` is an assertion you must be able to support from a document you have read or a drill you have run.
- Portfolio inventory: wallet counts, blockchain networks, and representative per-network gas costs.
- Awareness that the scoring weights are **engineering defaults with no external standards basis**, listed in `references/standards.md` and intended for recalibration against your own drill outcomes.

## Workflow

1. **Construct the Custodian Profile from Artefacts**: Instantiate `CustodyProviderProfile` with the architecture (`MULTISIG_ON_CHAIN`, `MPC_THRESHOLD`, `PROPRIETARY_VAULT`), the formats the custodian will contractually export, and the two decisive flags — `open_source_recovery_tool_available` (an offline path that runs without vendor involvement and whose source can be reviewed) and `requires_vendor_active_api_for_exit` (the insolvency-exposure flag).
2. **Separate Key Material from Derivation Metadata**: `BIP32_HD_PATH` is not an export of keys. If the declared formats contain no secret-bearing format, the score is 0 and the risk level is `CRITICAL` regardless of tooling or API independence — a recovery tool for material you cannot obtain recovers nothing.
3. **Define Portfolio Scope**: Construct `AssetPortfolio` with total value, wallet counts, networks, and average gas fees. Negative values raise `CustodyAnalyzerError` rather than producing a negative exit cost.
4. **Execute the Lock-In Assessment**: Call `evaluate_custody_provider(provider, portfolio)`. Read the `risk_factors` list, not just the score — the caveats that a single number cannot express (undisclosed derivation paths, SLIP-0039's deliberate BIP-39 incompatibility, WIF's per-address enumeration requirement) appear only there.
5. **Run the Drill in the Scenario That Matters**: `simulate_disaster_recovery_drill(provider, is_vendor_responsive=False)`. A success with `is_vendor_responsive=True` proves only that the vendor's export button works while the vendor is solvent, and the engine says so explicitly in its return message.
6. **Confirm the Recovery Package Is Complete**: Before accepting a `LOW` rating, derive a known funded address from the backup you actually hold. A seed plus the wrong derivation assumption locates no funds.
7. **Formulate Contract Remediation**: Apply the `recommendations` (offline open-source recovery tooling, cold key share extraction taken while the vendor is still operational, written derivation paths and script types per network) before executing the SLA.

> Full procedure: see `references/workflows.md`.
> Standards, sources and heuristic weights: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Online Portals with Key Sovereignty**: Being able to export CSV transactions or initiate web withdrawals does NOT constitute key sovereignty. If the vendor shuts down, assets are locked unless secret-bearing material is already held offline. This is why an offline drill that still depends on `requires_vendor_active_api_for_exit=True` is scored as a failure.
- **Treating a Derivation Path as a Key Export**: A `m/44'/60'/0'/0/0` path discloses no secret. Conversely, BIP-39 defines only mnemonic-to-seed and leaves wallet structure to BIP-32/44, so a seed **without** the custodian's derivation paths and script types can leave funds unfindable on non-default conventions. You need both halves; neither alone is a recovery package.
- **Assuming All Proprietary MPC Shares Are Unrecoverable**: This varies by vendor and must be verified, not assumed in either direction. Some MPC custodians publish offline recovery utilities that reconstruct extended private keys without the vendor — Fireblocks publishes one under GPL-3.0 and BitGo publishes an unsigned-sweep recovery tool that builds transactions independently of BitGo services (both verified 2026-09; re-verify, since vendor tooling changes). Others distribute shares in encodings that cannot be combined without a closed vendor binary. The test is whether *you* have run the offline reconstruction, not what the format is called.
- **Assuming Every Open Standard Is Equally Portable**: SLIP-0039 states it is "mainly intended as a replacement for BIP-0039 and for the most part, the two are not compatible" — SLIP-0039 shares restore only in wallets implementing SLIP-0039. WIF exports one key per address with no HD structure, so an export that misses an address silently strands its funds.
- **Failing to Execute Recovery Drills**: Accepting vendor key backup promises without a periodic offline reconstruction drill means discovering an unusable backup during the emergency. Drill the vendor-offline scenario, on the backup medium you actually hold, with the vendor uninvolved.
- **Underestimating Multi-Chain Exit Gas Costs**: Migrating thousands of wallets across 10+ EVM/UTXO networks incurs substantial on-chain gas and block-delay friction. The engine's estimate assumes one sweep per wallet per network, which is an upper bound when wallets are not funded on every network.

## Verification

Run the unit test suite, which covers open-standard and proprietary classification, the derivation-metadata-only and non-exportable cases, scoring monotonicity, input validation, exit cost and timeline arithmetic, and each disaster recovery drill branch:

```bash
python -m unittest discover -s skills/vendor-lock-in-risk-for-proprietary-custody-formats/scripts
```

Confirm structural and cross-reference validity with `python tools/validate_skills.py`.

## Related Skills

- `custody-solution-vendor-due-diligence-checklist`
- `third-party-custody-audit-report-review-cadence`
- `recovery-plan-for-lost-or-compromised-keys`
- `multi-party-computation-mpc-custody-solutions`
- `shamir-secret-sharing-for-key-backup`
- `air-gapped-signing-workflow-for-cold-storage`
- `vendor-outage-fallback-data-source-hierarchy`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `test-transaction-verification-before-large-transfers`
