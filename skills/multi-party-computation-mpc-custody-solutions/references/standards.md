# Standards — multi-party-computation-mpc-custody-solutions

## Scope and footing

Threshold ECDSA is a cryptographic engineering choice, not a regulated control with a
prescribed configuration. **No regulator mandates a threshold, a refresh interval, or
a shard topology**, and nothing below should be read as a compliance requirement. The
custody *regime* obligations — qualified custodian status, SOC reporting, insurance —
live in `custody-solution-vendor-due-diligence-checklist` and
`regulatory-custody-requirements-by-jurisdiction`.

## There is no NIST-approved threshold signature scheme

This is the single most consequential standards fact for institutional MPC custody,
and it is routinely misstated in vendor material.

- **NIST IR 8214C, "NIST First Call for Multi-Party Threshold Schemes"**, was published
  **final in January 2026**. It is a *call for public submissions*, not a standard.
- Consequently the threshold protocol itself **cannot be FIPS-validated** the way an
  HSM's cryptographic module can. A vendor's FIPS 140-3 certificate covers a module in
  their stack; it does not certify the MPC protocol.
- Protocol assurance therefore rests on peer-reviewed analysis plus the
  implementation's disclosed vulnerability posture — which is why the engine requires
  an explicit hardening attestation and denies without one.

Source: [NIST IR 8214C (final)](https://csrc.nist.gov/pubs/ir/8214/c/final),
[NIST Multi-Party Threshold Cryptography project](https://csrc.nist.gov/Projects/threshold-cryptography).

## Protocols

| Protocol | Paper | Signing rounds | Notable properties |
|---|---|---|---|
| **CGGMP21** ("CMP") | Canetti, Gennaro, Goldfeder, Makriyannis, Peled — [eprint 2021/060](https://eprint.iacr.org/2021/060) | 4-round variant = 3-round presigning + 1-round online; 7-round variant = 6 + 1 | UC-secure, proactive refresh, identifiable aborts, adaptive corruption |
| **GG18** | Gennaro, Goldfeder 2018 | 9 | First practical t-of-n ECDSA with no trusted dealer; no identifiable abort |
| **GG20** | Gennaro, Goldfeder 2020 | 7 | Adds non-interactive online signing and identifiable abort |

Only the *last* CGGMP21 round needs the message, so presignatures can be produced
before the transaction exists. That is a latency property, not a security one — a
stockpile of presignatures is signing capability sitting at rest.

Sources: [CGGMP21 eprint 2021/060](https://eprint.iacr.org/2021/060),
[TÜBİTAK BİLGEM — A Comparative Examination of Some Threshold ECDSA Protocols Used in Custody](https://blokzincir.bilgem.tubitak.gov.tr/en/a-comparative-examination-of-some-threshold-ecdsa-protocols-used-in-custody/).

## Disclosed key-extraction attacks — the reason hardening is attested, not assumed

### CVE-2023-33241 (GG18 / GG20 Paillier key vulnerability)

- Disclosed **2023-08-09** by Fireblocks, after a 90-day responsible disclosure begun
  2023-05-05.
- The flaw is **in the specification**, at the pseudocode level: parties do not check
  that a counterparty's Paillier modulus `N` is a biprime free of small factors. A
  malicious party injects a malformed modulus and cheats in the range proof.
- Impact: **full extraction of the other parties' key shares**. Depending on the
  implementation's beta parameter, as few as **16 signatures** suffice.
- Mitigation: key generation must detect a maliciously formed Paillier modulus using a
  suitable zero-knowledge proof. Many affected libraries are no longer maintained —
  confirm your specific version is patched.
- CGGMP21 is **not** in scope of this CVE.

Source: [Fireblocks — GG18 and GG20 Paillier Key Vulnerability (CVE-2023-33241) Technical Report](https://www.fireblocks.com/blog/gg18-and-gg20-paillier-key-vulnerability-technical-report),
[GitHub Advisory GHSA-5cjx-95fx-68q9](https://github.com/advisories/GHSA-5cjx-95fx-68q9).

### TSSHOCK (Verichains, Black Hat USA 2023, presented 2023-08-10)

- A family of **implementation** key-extraction attacks confirmed against GG18, GG20
  **and CGGMP21** libraries in Go and Rust.
- Several affected implementations **had already passed security audits**.
- Working proof-of-concept extracted a full private key by a single malicious party in
  **1–2 signing ceremonies**, leaving no trace.
- The load-bearing lesson: choosing CGGMP21 over GG18 does not exempt an
  implementation, and an audit letter is not a patch level.

Source: [Verichains — TSSHOCK](https://verichains.io/tsshock/),
[Verichains disclosure post](https://blog.verichains.io/p/verichains-discovers-critical-key).

## Engineering standards enforced by the engine

| Control | Standard | Basis |
|---|---|---|
| Threshold bounds | `2 <= t <= N`, `N >= 3` | `t = 1` lets one compromised shard sign; `t = 0` authorises an empty ceremony; `t > N` is unsatisfiable |
| Key material isolation | The policy layer never receives share material | A component that can receive shares can be made to reconstruct the key |
| Signature production | The policy layer emits **no** signature | Threshold ECDSA output can only come from the protocol; a synthesised `(r, s)` is a fabricated value |
| Shard independence | Attesting quorum spans `>= min_distinct_failure_domains` (default `t`) | `t` shards in one blast radius make the threshold decorative |
| Roster integrity | Out-of-roster attestation denies the request | Removing a party from a t-of-N group requires resharing, not an allowlist edit |
| Epoch agreement | All attesting shards on `current_key_epoch` | A PSS refresh invalidates every prior share |
| Library posture | CVE-2023-33241 and TSSHOCK attestations, both defaulting to `False` | Deny by default; see the two sections above |

`refresh_interval_days` (default 90.0) and `min_distinct_failure_domains` are
**engineering defaults with no regulatory basis**. Calibrate them to your mandate and
record the calibration.

## Threshold semantics and what t-of-N actually buys

`threshold_t` here means "t shards are required to sign", matching the `t-of-n` usage
in the GG18 and CGGMP21 literature. Note the two-sided consequence, which a single
number hides:

- **Confidentiality**: these are dishonest-majority protocols. The key is protected
  only while **fewer than `t`** shards are compromised. An attacker reaching `t` has
  full, silent signing capability.
- **Availability**: losing **more than `N - t`** shards makes the wallet permanently
  unspendable. `t = N` therefore has no fault tolerance at all, and the engine warns
  when it is configured.

## MPC versus on-chain multisig

MPC output is an **ordinary single ECDSA signature**. The chain records neither the
quorum size nor which shards participated. That buys chain-agnostic portability and
privacy, and costs on-chain enforceability: all policy and every audit record live in
your infrastructure, so a compromised policy service leaves nothing on-chain to
distinguish its transfers from legitimate ones. Where publicly verifiable approval
policy is the requirement, a native multisig or smart-contract wallet is the right
instrument.

Source: [Fireblocks — MPC vs Multi-Sig for Digital Asset Custody Security](https://www.fireblocks.com/blog/mpc-vs-multi-sig).
