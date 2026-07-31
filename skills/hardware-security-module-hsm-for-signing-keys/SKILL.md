---
name: hardware-security-module-hsm-for-signing-keys
description: >-
  Institutional crypto custody engine for managing non-exportable hardware signing keys, executing FIPS 140-2 Level 3 HSM signatures (secp256k1/ed25519/HMAC), and logging tamper-proof audit trails.
domain: Crypto Custody & Security
subdomain: Hardware Security Modules (HSM) & PKCS#11 Key Isolation
tags: ["hsm", "pkcs11", "hardware-security", "fips-140-2", "secp256k1", "ed25519", "crypto-custody", "signing-keys"]
brokers_frameworks: ["PKCS#11 API", "AWS CloudHSM", "YubiHSM2", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional crypto custody platforms, automated order execution gateways, and multi-asset treasury systems. Storing private signing keys in software memory on web servers creates catastrophic theft risk from OS vulnerabilities or memory dumps. A Hardware Security Module (HSM) isolates signing keys inside FIPS 140-2 Level 3/4 hardware boundaries. Private keys NEVER leave the physical enclave; transactions are sent into the HSM via PKCS#11 APIs, signed inside hardware, and returned as cryptographic signatures ($\sigma$).

## Prerequisites

- HSM configuration (`hsm_slot_id`, `fips_level`, `key_alias`, `algorithm`: `SECP256K1`, `ED25519`, `HMAC_SHA256`).
- PKCS#11 pin/token authentication credentials and authorized caller roles.

## Workflow

1. **HSM Key Isolation & Non-Exportability Audit**:
   - Verify key pair exists inside non-exportable hardware slot. Reject any export attempts.
2. **Transaction Payload Hashing**:
   - Compute cryptographic hash (SHA256 / Keccak256) of raw transaction payload.
3. **Enclave Signature Generation**:
   - Pass payload hash into HSM via PKCS#11 `C_Sign` function.
   - Execute signature computation inside hardware enclave and return signature bytes $\sigma$.
4. **Audit Log Recording**: Output structured `HsmSigningAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Attempting Key Export**: Trying to extract private keys into application memory, violating FIPS 140-2 compliance.
- **Ignoring PKCS#11 Session Concurrency**: Squeezing all concurrent trading threads into a single un-synchronized PKCS#11 session slot, causing hardware bottlenecking.
- **Un-Audited Signature Requests**: Signing transaction payloads without logging caller identity, payload hash, and timestamp in tamper-evident logs.

## Verification

- Instantiate `HsmSigningManagerEngine`. Generate secp256k1 key `CUSTODY_HOT_01`. Test signature generation on 32-byte payload hash $\implies$ verify valid signature returned and private key export attempt is REJECTED by hardware policy.
- Run `python scripts/test_hardware_security_module_hsm_for_signing_keys.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `shamir-secret-sharing-for-key-backup`
---
