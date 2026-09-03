---
name: cross-chain-address-reuse-privacy-risk
description: >-
  Use when auditing whether reusing the same address across chains such as Ethereum,
  Arbitrum, Solana and Bitcoin lets an observer link a desk's activity, scoring the
  deanonymisation exposure of a wallet registry you supply.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: crypto-security, address-reuse, privacy-risk, hd-wallet, bip44, deanonymization, chainalysis
  brokers_frameworks: "BIP-44 Standard; Python Dataclasses"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in institutional crypto trading desks and automated bot architectures to audit wallet address reuse across multiple blockchain networks (Ethereum, Arbitrum, Solana, Bitcoin). Using identical public key addresses or static EVM `0x...` addresses across multiple chains enables on-chain analytics firms (Chainalysis, Elliptic, Nansen) to deanonymize proprietary trading strategies, track total fund AUM, and link private wallets to KYC exchange deposits. This module computes an Address Reuse Privacy Risk Score and recommends HD Wallet BIP-44 path isolation.

## When NOT to Use

- **As an on-chain scanner.** `CrossChainAddressPrivacyAuditor` reads only the registry
  you hand it via `register_wallet()`. It opens no RPC connection, queries no explorer
  and imports nothing outside the standard library, so an address the desk never
  registered is invisible to it and scores as no risk. Completeness of the registry is a
  precondition, not an output — reconcile it against the chains before trusting a low
  score.
- **As transaction or counterparty screening.** The score measures *your* deanonymisation
  exposure: how readily an outside observer can link your wallets to each other and to a
  KYC'd deposit. It says nothing about who you are transacting with. Sanctions and
  counterparty exposure are `sanctions-screening-for-counterparties-and-instruments`;
  behavioural anomaly detection on flows is
  `on-chain-transaction-monitoring-for-anomalies`.
- **As a key-management implementation.** BIP-44 path isolation is the module's
  *recommendation*; it derives no keys, generates no addresses and rotates nothing. The
  implementation belongs to `crypto-wallet-key-custody-security`,
  `hot-cold-wallet-split-for-trading-bots` and `shamir-secret-sharing-for-key-backup`.
- **As a bridge or protocol risk assessment.** Reusing one address across chains is a
  privacy problem; moving value between those chains is a solvency problem. See
  `cross-chain-bridge-risk-for-multi-chain-strategies`.
- **As an externally benchmarked score.** The 0-100 scale, the `HIGH_RISK` $\ge 70$ /
  `MEDIUM_RISK` $\ge 40$ bands and the `total_tracked_chains` denominator are this
  module's own conventions. No regulator, standards body or analytics vendor defines
  them, and a score is comparable only against other runs with the same configuration.

## Prerequisites

- Active wallet address registry containing `chain_id`, `address`, `public_key`, `is_kyc_linked`, and `wallet_label` attributes per `WalletAddressRecord` (empty, malformed, or duplicate registrations are rejected).
- `public_key` may be `None` when the key is **not yet revealed on-chain** — a Bitcoin P2PKH output keeps the key hashed until its first spend. Pass `None`, never a placeholder string: every record sharing a placeholder would be clustered together and a single KYC linkage would contaminate all of them.
- `chain_id` is a human-entered label, normalised for grouping by strip + case-fold, so `Ethereum` / `ethereum` / `" Ethereum "` are one chain.
- Risk score thresholds (defaults: `HIGH_RISK` $\ge 70.0$, `MEDIUM_RISK` $\ge 40.0$, both configurable) and the desk's `total_tracked_chains` denominator (default 5).

## Workflow

1. **Wallet Address Graph Ingestion**: Ingest wallet records across chains ($C_1, C_2, \dots, C_m$).
2. **Cross-Chain Linkage Detection**:
   - Cluster records transitively: identical addresses (case-insensitive ONLY for `0x` hex per EIP-55; base58 formats such as Bitcoin/Solana compared case-sensitively) OR identical public keys (links different address formats, e.g. a Bitcoin address to an EVM address sharing one secp256k1 key). A `None` public key forms **no** edge — two addresses are not linked merely because neither has revealed a key.
   - Identify clusters spanning $K > 1$ chains.
   - Detect if any address in the cluster has been linked to a KYC exchange deposit/withdrawal; one KYC linkage contaminates the whole cluster.
   - An address absent from the registry returns `NOT_TRACKED` (status unknown) — never interpret it as low risk.
3. **Privacy Risk Score Calculation**:
   - $\text{Reuse Weight} = 0$ when $K = 1$ (a single chain is by definition **no** reuse), else $\min\left(50.0, \frac{K_{\text{reused\_chains}}}{M_{\text{total\_chains}}} \times 50.0\right)$. Charging $K = 1$ would score a perfectly isolated address 50.0 (MEDIUM) on a desk configured with `total_tracked_chains=1`.
   - $\text{KYC Penalty} = 50.0$ if KYC-linked else $0.0$.
   - $\text{Risk Score} = \min(100.0, \text{Reuse Weight} + \text{KYC Penalty})$.
4. **Remediation Directive Generation**:
   - Enforce HD Wallet BIP-44 path separation: `coin_type` isolates chain families (Bitcoin $0'$, EVM $60'$, Solana $501'$ per SLIP-44), but an EVM key pair yields the identical `0x` address on every EVM network — EVM-to-EVM isolation requires distinct `account'` indexes or separate seeds.
   - Mandate unique address rotation per sub-account.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static 0x EVM Address Deployment**: Deploying the exact same bot address across 8 EVM chains, making all multi-chain trading volume publicly linkable to a single entity. EVM address derivation takes no chain parameter, so one key pair produces one identical address everywhere.
- **KYC Deposit Contamination**: Depositing funds from a private strategy address directly into a KYC exchange account, deanonymizing the entire cross-chain wallet cluster.
- **Ignoring Public Key Extraction**: Assuming different chain address formats (e.g. Bitcoin vs Ethereum) prevent linking, forgetting that spending transactions reveal the underlying secp256k1 public key — the auditor links such records via public-key clustering.
- **Lowercasing Addresses to Compare Them**: In base58 (Bitcoin legacy, Solana) both letter cases are distinct alphabet characters, so two addresses differing only by case are different addresses; case-insensitive comparison is valid only for `0x` hex (EIP-55 checksum).
- **Treating Untracked as Safe**: An address missing from the registry is `NOT_TRACKED` (unknown), not low risk — absence of data is not evidence of privacy.
- **Placeholder Public Keys**: filling `public_key` with `"UNKNOWN"` / `"N/A"` / `"TBD"` for keys not yet revealed on-chain makes every such record share a linkage edge. The auditor then reports one fictitious mega-cluster and propagates any single KYC linkage across wallets that have nothing to do with each other. Pass `None`.
- **Free-Text Chain Labels**: registering `Ethereum` and `ethereum` as separate labels used to count as two chains and manufacture a reuse finding for a single-chain wallet. Labels are now normalised, but keep them consistent — the registry is only as good as its identifiers.
- **Reading a LOW Score as "No Findings"**: a 2-chain cluster with no KYC scores 20.0 → `LOW`, yet the reuse is real. Read `remediation_actions`, not just `risk_level`; the module no longer appends a clean bill of health when findings exist.

## Verification

- Instantiate `CrossChainAddressPrivacyAuditor`. Register wallet `0x123...abc` active on 5 EVM chains (`Ethereum`, `Arbitrum`, `Optimism`, `Polygon`, `BSC`). Mark 1 address as KYC-linked on Binance. Verify auditor flags a `HIGH_RISK` (Risk Score = 100.0) deanonymization alert and recommends BIP-44 path isolation.
- Register a Bitcoin base58 address and an EVM `0x` address sharing one `public_key`; verify both are linked into a single 2-chain cluster with a `PUBLIC KEY LINKAGE` remediation.
- Audit an unregistered address; verify the verdict is `NOT_TRACKED`, not `LOW`.
- Register a single isolated address on an auditor with `total_tracked_chains=1`; verify the score is `0.0`/`LOW`, not `50.0`/`MEDIUM`.
- Register two `public_key=None` addresses on different chains, one KYC-linked; verify they do **not** cluster and the KYC flag does not propagate.
- Register a 2-chain reused address with no KYC (score 20.0, `LOW`); verify `remediation_actions` reports the reuse and does **not** claim "strong cross-chain privacy isolation".
- Run `python -m unittest discover -s skills/cross-chain-address-reuse-privacy-risk/scripts`.

## Related Skills

- `phishing-resistant-authentication-for-custody-access`
- `on-chain-transaction-monitoring-for-anomalies`
---
