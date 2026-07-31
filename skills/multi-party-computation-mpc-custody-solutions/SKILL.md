---
name: multi-party-computation-mpc-custody-solutions
description: >-
  Multi-Party Computation (MPC) threshold signature custody engine evaluating t-of-N secret sharing (CMP/GG18 protocols), quorum verification, and zero-knowledge key security.
domain: Crypto Custody Security
subdomain: Threshold Cryptography & Multi-Party Computation Custody
tags: ["mpc", "custody", "threshold-signature", "tss", "cmp-protocol", "gg18", "shamir-secret-sharing", "crypto-security"]
brokers_frameworks: ["CMP Threshold Protocol", "GG18 ECDSA", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing enterprise crypto asset custody solutions for automated trading bots and institutional funds. Traditional single-key hot wallets introduce a single point of failure (SPOF) where private key memory exposure can compromise millions in funds. Multi-Party Computation (MPC) divides private keys into $N$ secret shards held across independent nodes (e.g., Trading Bot Node, Custodian Cloud, HSM). Using threshold signature protocols (CMP / GG18 $t$-of-$N$, e.g. 2-of-3 threshold), transaction signatures $(r, s)$ are generated collaboratively without ever reconstructing the private key in memory.

## Prerequisites

- MPC cluster configuration (`num_shards`: $N=3$, `threshold_t`: $t=2$, `protocol`: `'CMP'`, `authorized_nodes`).
- Transaction signing request payload (`tx_hash`, `destination_address`, `amount_usd`, `partial_share_submissions`).

## Workflow

1. **Threshold Quorum Verification**:
   - Verify submitted partial key shares against authorized nodes.
   - Assert submitted share count meets threshold:
     $$\text{Valid\_Shares} \ge t$$
     If $< t \implies$ Reject (`MPC_THRESHOLD_NOT_MET`).
2. **Zero-Knowledge Threshold Signature Assembly**:
   - Aggregate partial signature shares into a valid blockchain ECDSA signature $(r, s)$ without revealing individual key shares or assembling the full private key.
3. **Proactive Secret Sharing (PSS) Key Refresh**:
   - Audit periodic key share refresh cycles where nodes generate new mathematical shares without changing the public wallet address.
4. **Audit Report Generation**: Output structured `MPCSigningReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reconstructing Key in Memory**: Flawed implementations that collect all shares and assemble the private key in RAM prior to signing, restoring the single point of failure.
- **Node Co-location**: Hosting multiple MPC shard nodes in the same cloud region or provider, leaving the cluster vulnerable to single-vendor breaches.
- **Lacking Proactive Key Refresh**: Failing to periodically refresh key shares, enabling an attacker to compromise shards over an extended period.

## Verification

- Instantiate `MPCCustodyEngine`. Audit 2-of-3 CMP signing request for $100k transfer $\implies$ submit 2 valid shares $\implies$ verify signature assembly $(r, s)$ and status `MPC_SIGNING_SUCCESS`. Submit 1 share $\implies$ verify rejection `MPC_THRESHOLD_NOT_MET`.
- Run `python scripts/test_multi_party_computation_mpc_custody_solutions.py`.

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `hardware-security-module-hsm-for-signing-keys`
---
