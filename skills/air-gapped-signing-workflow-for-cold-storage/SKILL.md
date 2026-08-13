---
name: air-gapped-signing-workflow-for-cold-storage
description: Models a strict air-gapped Ethereum-style signing workflow (Coordinator -> QR/SD -> Offline Vault -> Sign -> Broadcast) for institutional crypto custody.
domain: crypto-custody-security
subdomain: Key Management
tags:
- crypto
- custody
- security
- air-gap
- clear-signing
brokers_frameworks:
- Generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill when designing institutional crypto treasury workflows or securing API withdrawal processes. High-value signing must never occur on internet-connected machines. This reference separates the online `OnlineCoordinator` from the offline vault and requires clear signing before approval.

## Prerequisites

- Python 3.9+
- Understanding of Ethereum-style addresses, nonces, and chain/network identifiers.
- Production deployments must use audited chain-native transaction parsers, hardware wallets or HSMs, and real public-key signatures. The included signature primitive is an educational test seam only.

## Workflow

1. **Coordinator (Online)**: Generate and retain a validated, versioned unsigned intent with destination, decimal amount, network, and nonce.
2. **Canonical Export**: Serialize the exact intent deterministically and transfer it only through controlled QR or clean SD media. Do not use USB, Bluetooth, Wi-Fi, or cellular bridges.
3. **Signer (Offline)**: Decode and validate the payload, display the exact destination and amount, apply local policy, and obtain human approval before signing.
4. **Signed Return**: Return the signed envelope containing the canonical payload, payload hash, signature, and signer key identifier through the controlled medium.
5. **Independent Verification**: The coordinator recomputes the payload hash, checks that the intent was issued by this coordinator, validates signer identity, and verifies the signature before any broadcast adapter is called.
6. **Idempotent Broadcast**: Reject replayed or duplicate payloads. Treat ambiguous RPC outcomes as unresolved until reconciled; never blindly retry an unknown order state.

## Common Pitfalls

- **Blind Signing**: Signing a hash without decoding and displaying the destination and amount allows a compromised coordinator to redirect funds.
- **Network Bridging**: USB cables, Bluetooth, Wi-Fi, or cellular connectivity defeat the air gap.
- **Unbound Envelopes**: Accepting a signature without binding it to the exact coordinator-issued payload permits substitution.
- **Replay**: Broadcasting a valid signed payload more than once can create duplicate operational actions.
- **Demo Cryptography in Production**: The standard-library HMAC-style primitive in this reference is not asymmetric custody cryptography and must not protect production funds.

## Verification

Run `python scripts/test_air_gapped_signing_workflow_for_cold_storage.py` or `python -m unittest discover -s skills/air-gapped-signing-workflow-for-cold-storage/scripts` to verify successful transfers, canonicalization, tamper rejection, and replay controls.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `multi-signature-approval-for-large-transfers`
