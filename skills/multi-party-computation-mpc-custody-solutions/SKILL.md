---
name: multi-party-computation-mpc-custody-solutions
description: >-
  Use when a key is split across independent parties by a threshold signature scheme,
  validating the t-of-N shard roster, rejecting out-of-roster or cloned attestations and
  enforcing key epochs. It authorises; it does not sign.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: mpc, custody, threshold-signature, tss, cggmp21, gg18, proactive-secret-sharing, crypto-security
  brokers_frameworks: "CGGMP21 / CMP Threshold ECDSA (eprint 2021/060); GG18 / GG20 Threshold ECDSA; CVE-2023-33241 (Paillier key vulnerability); TSSHOCK (Verichains, Black Hat USA 2023); NIST IR 8214C (Threshold Call); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading bot or treasury system moves crypto with a key that is
split across independent parties by a threshold signature scheme (Fireblocks, Coinbase
MPC, ZenGo, or a self-operated CGGMP21/GG18 library). A single-key hot wallet loses
everything to one memory disclosure; a t-of-N group means no host ever holds a
complete key, and signing happens through a multi-round protocol between the shards.

`MPCCustodyEngine` is the **policy and audit gate in front of that protocol**. It
decides whether a signing ceremony may start and leaves an auditable record of the
requests it refused, checking, in order: shard roster membership, one-shard-one-vote
(including a cloned shard running on two hosts), proactive-secret-sharing key epoch
agreement, the t-of-N quorum count, failure-domain independence of the attesting
quorum, and whether the operator has attested that their MPC library carries the 2023
key-extraction fixes.

## When NOT to Use

- **As a threshold signature implementation.** This engine produces no signature and
  no signature-like value, deliberately. Threshold ECDSA is an interactive multi-round
  protocol between shard holders; a policy layer that synthesises `(r, s)` is emitting
  a fabricated value that no chain will accept. Use a maintained MPC library — and
  never a hand-rolled one.
- **Anywhere key-share material would have to be handed to it.** `MPCShardAttestation`
  has no field for share material and never will. A policy layer that can receive
  shares is a policy layer that can be made to reconstruct a key.
- **For value tiering, timelocks, or approver governance.** See
  `multi-signature-approval-for-large-transfers`. `amount_usd` and
  `destination_address` are carried here as audit context only, and every report says
  so in `warnings`.
- **For destination allow-listing.** See `exchange-withdrawal-whitelist-enforcement`.
- **Where on-chain, publicly verifiable approval policy is the requirement.** MPC
  output is an ordinary single signature, so the chain records neither the quorum nor
  which shards participated. If the policy must be enforced and audited by the chain
  itself, a native multisig or smart-contract wallet is the right instrument, and this
  engine's audit trail is not a substitute.
- **As a compliance attestation.** There is no NIST-approved threshold signature
  scheme to conform to — see `references/standards.md`.

## Prerequisites

- A shard roster (`MPCShardNode`) with a **`failure_domain` per node**: the cloud
  account, provider, or operator whose compromise yields control of that node. Two
  regions of one AWS account are one domain; a self-operated HSM is another.
- `threshold_t` and the roster length N, satisfying `2 <= t <= N` and `N >= 3`. The
  engine raises rather than accepting a policy it cannot honour.
- `current_key_epoch`, bumped on every successful proactive secret sharing (PSS)
  refresh, and `last_key_refresh_date` — without the date, refresh cadence is
  unverifiable and every report says so.
- Positive hardening attestations. `implementation_hardened_against_cve_2023_33241`
  and `implementation_hardened_against_tsshock` default to `False`, so **the default
  configuration denies**. Confirm your library's patched version first; do not set
  these to clear a denial.
- Authenticated transport underneath the engine. It trusts that an attestation really
  came from the node it names; mutual TLS or signed attestations must sit below it.
- Per-node commitments derived deterministically from each shard's **public** key
  share — use `derive_shard_attestation_commitment`. Randomised per-ceremony values
  defeat cloned-shard detection.

## Workflow

1. **Validate the Quorum Policy at Construction, Not at Signing Time**: `t < 2` lets a
   single compromised shard sign and `t = 0` would authorise a ceremony with no
   participants at all; `t > N` is unsatisfiable; a duplicate `node_id` means one party
   holds two votes. All raise `MPCCustodyConfigError`. N is derived from the roster so
   a shard count can never disagree with it.
2. **Reject an Out-of-Roster Attestation Outright — Do Not Drop It Silently**:
   Removing a party from a t-of-N group requires a resharing, not an allowlist edit, so
   an attestation from a node outside the roster is either a misconfiguration or an
   adversary probing the ceremony. Fail the whole request (`MPC_UNAUTHORIZED_NODE`)
   even when the remaining shards would have met the threshold; a quorum assembled
   alongside a rogue submitter is not evidence of a healthy group.
3. **Count Shards, Not Messages**: A repeated `node_id` is a double-count attempt. An
   identical `share_commitment` from two *different* node ids means one shard was
   restored onto a second host — the quorum then represents fewer independent parties
   than it claims, which is exactly the property t-of-N is bought for. Both deny with
   `MPC_DUPLICATE_SHARD_ATTESTATION`.
4. **Require One Key Epoch Across the Quorum**: A PSS refresh invalidates every prior
   share. A mixed-epoch quorum means a stale shard is participating; the protocol would
   abort anyway, and denying first turns a confusing abort into a named finding
   (`MPC_KEY_EPOCH_MISMATCH`).
5. **Check the Threshold, Then Check Independence**: `t` shards sitting in one failure
   domain make the threshold decorative — one account compromise signs. The attesting
   quorum must span `min_distinct_failure_domains` (default `t`) distinct domains.
6. **Refuse to Sign on an Unattested Library**: GG18 and GG20 carry CVE-2023-33241 at
   the *specification* level, and TSSHOCK broke audited GG18, GG20 **and CGGMP21**
   implementations, so protocol choice is not a security posture. Both attestations
   default to `False` and their absence denies (`MPC_PROTOCOL_NOT_HARDENED`).
7. **Report Every Failure, Route on One**: All checks run. `findings` carries every
   defect; `status` names the highest-priority one, so an alert routes on a single
   field without losing the audit trail.
8. **Audit Report Generation**: Output a structured `MPCSigningAuthorizationReport`.
   `is_authorized=True` means "start the ceremony in the MPC library" — never "a
   signature exists".

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Synthesising a Signature in the Policy Layer**: Hashing the collected shares into
  an `(r, s)`-shaped pair produces a value that is not a signature, that no chain will
  verify, and whose derivation just combined the shares in one process — restoring the
  single point of failure the architecture exists to remove. This engine returns an
  authorisation decision and no signature fields at all.
- **Handing Key-Share Material to the Policy Layer**: Any component that can *receive*
  shares can be made to reconstruct the key. Pass a non-secret commitment over the
  node's public key share instead; the dataclass has no field for anything else.
- **Treating CMP as "The Safe One"**: CGGMP21 is genuinely better than GG18 — 4 rounds
  (3-round presigning plus a 1-round online phase) against GG18's 9, with identifiable
  aborts and proactive refresh built in. But Verichains extracted keys from *audited*
  CGGMP21 libraries in 1–2 ceremonies (TSSHOCK, Black Hat USA 2023). The protocol on
  the whiteboard is not the library in production.
- **Assuming an Audit Means the Library Is Safe**: Both 2023 attack families landed on
  implementations that had already passed security audits, and CVE-2023-33241 was a
  defect in the published pseudocode itself, not in one vendor's code. Track the
  patched version, not the audit letter.
- **Node Co-location**: Three shards in three regions of one cloud account is one
  compromise away from a full quorum. Independence is about the account, provider, and
  operator — not the region.
- **Letting Key Shares Age**: Without proactive refresh an attacker can compromise
  shards one at a time across an unbounded window and only needs to reach `t`. The
  refresh must bump `current_key_epoch`, and every node must re-attest on the new one.
- **Reading `t`-of-N as a Security Level**: These are dishonest-majority protocols.
  A 2-of-3 group is protected only while fewer than two shards are compromised; an
  attacker who reaches `t` has full, silent signing capability and the chain shows an
  ordinary transaction.
- **Expecting the Chain to Enforce the Quorum**: MPC policy lives entirely off-chain.
  A compromised policy service that authorises a ceremony leaves nothing on-chain to
  distinguish it from a legitimate transfer.

## Verification

- Authorise a 2-of-3 CMP ceremony with both hardening attestations set, two
  same-epoch attestations from distinct failure domains, and confirm
  `MPC_SIGNING_AUTHORIZED` with `distinct_failure_domains == 2` — and that the report
  has no `signature_r`, `signature_s`, or `is_signed` attribute at all.
- Submit one attestation and confirm `MPC_THRESHOLD_NOT_MET`.
- Construct `MPCCustodyConfig(threshold_t=0)` and confirm `MPCCustodyConfigError`
  rather than an authorisation granted on zero participants.
- Add an out-of-roster `ROGUE_NODE_99` alongside two valid attestations and confirm
  `MPC_UNAUTHORIZED_NODE` — the two valid shards must **not** carry the request.
- Give two distinct node ids the same `share_commitment` and confirm
  `MPC_DUPLICATE_SHARD_ATTESTATION` with `accepted_attestation_count == 0`.
- Put two roster nodes in one `failure_domain`, attest with exactly those two, and
  confirm `MPC_FAILURE_DOMAIN_CONCENTRATION`; swap one for the third node and confirm
  the same roster now authorises.
- Set `protocol="GG18"` without the CVE attestation, and separately `protocol="CMP"`
  without the TSSHOCK attestation, and confirm `MPC_PROTOCOL_NOT_HARDENED` in both.
- Submit an empty or non-hex `share_commitment` and confirm `MPCSigningRequestError`
  rather than a share counted toward the quorum.
- Run `python -m unittest discover -s skills/multi-party-computation-mpc-custody-solutions/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `hardware-security-module-hsm-for-signing-keys`
- `multi-signature-approval-for-large-transfers`
- `exchange-withdrawal-whitelist-enforcement`
- `key-rotation-schedule-for-hot-wallet-keys`
- `recovery-plan-for-lost-or-compromised-keys`
- `custody-solution-vendor-due-diligence-checklist`
- `segregation-of-duties-for-custody-operations`
