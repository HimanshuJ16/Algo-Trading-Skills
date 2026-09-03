# Standards — vendor-lock-in-risk-for-proprietary-custody-formats

## Scope and status of everything below

The key format specifications are **normative** — they say what a format is and is
not. The scoring weights and thresholds are **repo heuristics with no external
standards basis**; they are documented here so they can be argued with and
recalibrated. The regulatory section lists *touchpoints* only: custody
obligations differ by jurisdiction and none of them is decided by a score.
Nothing here is legal advice.

---

## 1. Key Format Classification Matrix

Portability is not binary and "open standard" is not a synonym for "restorable
anywhere". The last column is the one that matters in a drill.

| Key Format Type | Standard | Carries a secret? | Real-world portability |
| :--- | :--- | :--- | :--- |
| **BIP39_MNEMONIC** | Open ([BIP-39](https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki)) | Yes (seed) | Broadest cross-wallet support of any backup format. Restoration still needs the derivation path/script type, and the optional passphrase if one was set. |
| **SLIP39_SHAMIR** | Open ([SLIP-0039](https://github.com/satoshilabs/slips/blob/master/slip-0039.md)) | Yes (seed, split into shares) | Standardised group/member threshold recovery, but restorable **only** in wallets implementing SLIP-0039. The spec states it "is mainly intended as a replacement for BIP-0039 and for the most part, the two are not compatible." |
| **BIP32_HD_PATH** | Open ([BIP-32](https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki), [BIP-44](https://github.com/bitcoin/bips/blob/master/bip-0044.mediawiki), [SLIP-0044](https://github.com/satoshilabs/slips/blob/master/slip-0044.md)) | **No** | Metadata, not key material. Necessary to locate funds, never sufficient to move them. A custodian exporting only paths exports nothing. |
| **WIF_PRIVATE_KEY** | Open ([Wallet Import Format](https://en.bitcoin.it/wiki/Wallet_import_format)) | Yes (one finished key) | Directly importable and needs no derivation path, but is one key per address with no HD structure: the export must enumerate every address in use. |
| **PROPRIETARY_MPC_SHARE** | Vendor-specific | Yes (share) | Depends entirely on whether an offline reconstruction tool exists and has been drill-tested. See §2 — this is genuinely vendor-by-vendor, in both directions. |
| **PROPRIETARY_HSM_BLOB** | Vendor-specific | Yes, but enclave-bound | Non-exportable by construction; scored as zero portability. |

Why BIP-39 alone is not a complete recovery package: BIP-39 defines mnemonic →
seed and states only that "this seed can be later used to generate deterministic
wallets using BIP-0032 or similar methods". Account index, coin type and script
type are set by the *wallet*, not the mnemonic, so a custodian that used a
non-default convention can hand over a valid seed from which you find nothing.

---

## 2. Proprietary MPC shares — verify per vendor, assume nothing

The claim "MPC shares cannot be recombined without the vendor's closed binary" is
true of some custodians and false of others, and getting it wrong in either
direction is expensive. Two counter-examples, both verified 2026-09 and both
subject to change:

- **Fireblocks** publishes [`fireblocks/recovery`](https://github.com/fireblocks/recovery),
  an airgapped desktop Recovery Utility licensed **GPL-3.0-or-later** that
  recovers extended private keys from a Recovery Kit for use outside Fireblocks.
- **BitGo** publishes [`BitGo/wallet-recovery-wizard`](https://github.com/BitGo/wallet-recovery-wizard),
  which supports an **unsigned sweep** built from the user and backup keys
  "independent from any BitGo services", and non-BitGo recovery using the
  recovery KeyCard.

The existence of a published tool is a starting point, not the finding. What the
assessment records is whether *you* have executed the offline reconstruction on
the backup you actually hold. Re-verify tooling at each review cycle: a
repository, its licence, and its supported chains can all change.

---

## 3. Lock-In Risk Classification — the rule the engine actually applies

Portability score, 0–100, computed in `evaluate_custody_provider`:

1. If **no secret-bearing format** is exportable (all formats are
   `PROPRIETARY_HSM_BLOB`, derivation metadata only, or the list is empty), the
   score is **0.0** and the level is **CRITICAL**. The bonuses below are gated on
   exportable material, because tooling for material you cannot obtain recovers
   nothing.
2. Otherwise: `score = 50 × best_format_portability + 30 (independent offline
   recovery tool) + 20 (exit does not require an active vendor service)`.

`best_format_portability` is the **maximum** over the secret-bearing formats
offered, not the mean. Using the mean made the score non-monotonic: a custodian
that declared extra proprietary export options scored *worse* than an otherwise
identical one, despite the open export path being unchanged.

| Format | Weight | Rationale (ordinal, not measured) |
| :--- | :--- | :--- |
| `BIP39_MNEMONIC` | 1.00 | Widest wallet implementation base. |
| `SLIP39_SHAMIR` | 0.90 | Open and recovers the whole HD tree, but a narrower implementation base and not BIP-39 compatible. |
| `WIF_PRIVATE_KEY` | 0.85 | Universally importable but per-address and non-hierarchical. |
| `PROPRIETARY_MPC_SHARE` | 0.30 | Recovery hinges on the tool attestation rather than the format. |
| `PROPRIETARY_HSM_BLOB` | 0.00 | Non-exportable. |

| Lock-In Risk Level | Rule |
| :--- | :--- |
| **LOW** | Score ≥ 85 **and** open standard ratio ≥ 75% **and** the estate is locatable — i.e. derivation metadata is disclosed, or the best format is not seed-derived (WIF needs no path). |
| **MEDIUM** | Score ≥ 60 and not LOW. Includes a seed export with undisclosed derivation paths, and a proprietary MPC share with a drill-tested offline tool and no vendor-service dependency (50 × 0.30 + 30 + 20 = 65). |
| **HIGH** | Score ≥ 35. Recovery depends on vendor software or vendor availability. |
| **CRITICAL** | Score < 35, including every no-exportable-material case. |

The **Open Standard Compliance Ratio** is reported as a coverage diagnostic — the
share of declared formats defined by a public specification — and deliberately
does not drive the score, except as the secondary ≥ 75% condition on LOW.

---

## 4. Migration Cost & Duration

1. **Total exit cost**

   $$C_{\text{migration}} = \text{Fee}_{\text{vendor\_export}} + \left( W \times N \times \text{GasFee}_{\text{avg}} \right)$$

   where $W$ is wallet count and $N$ is supported networks. This assumes **one
   sweep transaction per wallet per network** at a single average fee — an upper
   bound whenever wallets are not funded on every network, and an underestimate
   where token approvals or contract interactions add transactions. Model
   per-network fees separately when the estate is concentrated on expensive
   chains.

2. **Total exit duration**

   $$T_{\text{migration}} = T_{\text{contractual\_notice}} + \Delta T_{\text{recovery\_tool}}$$

   where $\Delta T_{\text{recovery\_tool}} = 14$ days (`RECOVERY_TOOL_DELAY_DAYS`)
   when no independent offline recovery tool exists. That 14 is an engineering
   default with no contractual or regulatory basis — replace it with the notice
   and response periods in your own custody agreement.

---

## 5. Regulatory touchpoints

Jurisdiction-specific and non-exhaustive. These bear on custody arrangements and
exit rights; none of them prescribes a key format or a portability score.

- **EU — MiCA, Regulation (EU) 2023/1114, Article 75** ("Providing custody and
  administration of crypto-assets on behalf of clients"): requires a client
  agreement specifying duties and responsibilities (para. 1), a register of
  positions per client (para. 2), a **custody policy** with internal rules for
  safekeeping or control of the crypto-assets "or the means of access" (para. 3),
  procedures to return clients' crypto-assets as soon as possible (para. 6), and
  liability to clients for loss of crypto-assets or of the means of access,
  capped at the market value at the time of loss (para. 8). Source:
  [EUR-Lex — Regulation (EU) 2023/1114](https://eur-lex.europa.eu/eli/reg/2023/1114/oj/eng)
  (paragraph numbers taken from the consolidated article text; confirm against the
  OJ before relying on them in a filing). Note the recurring phrase *means of
  access* — MiCA's subject matter is precisely the key material this skill scores.
- **US (New York) — NYDFS**, "Updated Guidance on Custodial Structures for
  Customer Protection in the Event of Insolvency", issued **2025-09-30**,
  superseding the 2023-01-23 guidance. Applies to BitLicensees and NY limited
  purpose trust companies; covers segregation of customer virtual currency,
  permissible uses, sub-custodian arrangements and disclosure. Source:
  [DFS industry letter](https://www.dfs.ny.gov/industry-guidance/industry-letters/il20250930-updated-guidance-custodial-structures).
- **US (federal) — Advisers Act Rule 206(4)-2** remains the operative custody
  rule for SEC-registered advisers. The proposed **Safeguarding Rule** (proposed
  2023-02-15), which would have addressed crypto assets expressly, was
  **withdrawn on 2025-06-12**. Do not cite the proposal as a live requirement.
  See `custody-solution-vendor-due-diligence-checklist` for the qualified
  custodian analysis, including the 2025-09-30 staff no-action letter on
  state-chartered trust companies.

Regulatory protection and technical portability are different controls and do not
substitute for each other: a MiCA-compliant custodian that exports only
enclave-bound blobs still scores `CRITICAL` here, and correctly so.
