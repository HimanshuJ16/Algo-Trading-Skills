---
name: hardware-security-module-hsm-for-signing-keys
description: >-
  Use when irreversible on-chain transfers are authorised with keys inside a PKCS#11
  HSM, auditing the non-exportability attributes that prove the key cannot leave, and
  enforcing signing authorisation and rate limits.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: hsm, pkcs11, hardware-security, fips-140-3, secp256k1, ed25519, crypto-custody, signing-keys
  brokers_frameworks: "PKCS#11 v3.1 (OASIS); AWS CloudHSM (hsm2m.medium); YubiHSM 2 FIPS; NIST FIPS 140-3 / CMVP; BIP-146 / EIP-2; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading or treasury system authorises irreversible on-chain transfers with keys that live inside an HSM (AWS CloudHSM, YubiHSM 2, Thales/Entrust, or a custodian's PKCS#11 endpoint). Holding a private key in a web server's memory means an OS vulnerability or a core dump is a total loss; an HSM keeps the key inside a validated hardware boundary and exposes only `C_Sign`. The hard part is not buying the device — it is proving the key was never extractable, making sure the bytes you hand to `C_Sign` are the bytes the chain will verify against, and leaving an audit record of the signing requests you *refused* as well as the ones you granted.

`HsmSigningManagerEngine` is the policy and audit layer that sits between your execution logic and the vendor's PKCS#11 binding.

## When NOT to Use

- **As an HSM, a key store, or a signer.** This module holds no key material and computes no signatures. It wraps a `signer` callable that must be a real PKCS#11 `C_Sign` binding (python-pkcs11, PyKCS11, a vendor SDK). Version 1.x *simulated* signing by deriving a "private key" as `sha256(b"HSM_ENTROPY_SEED_" + alias)` and returning an HMAC — every such key was reconstructible from the alias alone and every signature was forgeable offline. Nothing built on 1.x output should be trusted.
- **As a signature validity check.** The engine verifies that a returned signature has the right length and, for secp256k1, that `r` and `s` are in range. It cannot verify a signature against a public key without curve arithmetic it deliberately does not implement. There is no `is_signature_valid` field, because the 1.x one was hard-coded `True`.
- **As proof that a key is non-exportable.** It audits attributes you supply. Those values must be read back from the device with `C_GetAttributeValue` — an attribute you typed into a config is a document, not a control.
- **As the approval gate for a large transfer.** Role authorisation here is bookkeeping in your process. Quorum and value thresholds belong in the HSM's own policy engine or the custodian's, outside anything the trading system can rewrite — see `multi-signature-approval-for-large-transfers`.
- **For keys that never touch an online system.** An offline signing ceremony has a different threat model; see `air-gapped-signing-workflow-for-cold-storage`.

## Prerequisites

- A key generated **on the device** (`C_GenerateKeyPair` with `CKA_SENSITIVE=True`, `CKA_EXTRACTABLE=False`), never imported from software.
- The key's attributes read back via `C_GetAttributeValue`: `CKA_SENSITIVE`, `CKA_EXTRACTABLE`, `CKA_NEVER_EXTRACTABLE`, `CKA_ALWAYS_SENSITIVE`.
- The module's **CMVP certificate number**, not the datasheet's marketing claim — plus its Historical List date if it is a FIPS 140-2 certificate.
- A PKCS#11 session, PIN/token authentication, and a `signer` callable with the signature `(HsmKeyMetaData, bytes) -> bytes`.
- An explicit evaluation clock (`current_time_epoch`) for every call, so audit records and FIPS findings are reproducible rather than dependent on wall time.

## Workflow

1. **Register the key from device-read attributes, and refuse to overwrite one.** `register_hardware_key` raises `HsmKeyAlreadyRegisteredError` on a duplicate alias. Decision point: this is deliberately *not* idempotent-by-overwrite. On a real HSM the equivalent mistake — generating over an existing label — destroys the only key that can sign for existing addresses, and silently replacing metadata hides a key swap. Take a new alias instead.
2. **Audit non-exportability on both attributes, not one.** `CKA_EXTRACTABLE=False` blocks `C_WrapKey`; `CKA_SENSITIVE=True` blocks reading the value with `C_GetAttributeValue`. They stop different attacks and neither implies the other. Then check the history: `CKA_NEVER_EXTRACTABLE=False` means the key *was* extractable at some point, so a wrapped copy may already exist — today's attribute values say nothing about copies already taken. Treat that as exposed material and rotate; it cannot be repaired, because `CKA_EXTRACTABLE` is one-way.
3. **Check FIPS validation currency against the certificate, not the label.** CMVP stopped accepting FIPS 140-2 submissions on 2022-04-01 and moves all remaining FIPS 140-2 certificates to the Historical List on 2026-09-22. Decision point: individual certificates sunset *earlier* — five years after validation — so AWS CloudHSM `hsm1.medium` (cert #4218) went historical on 2026-01-04, months ahead of the program-wide date. Pass the module's own `fips_historical_epoch`; the engine falls back to the program date only when you do not know it. Historical status is a migration finding, not an outage: CMVP still supports historical modules for existing systems.
4. **Declare what the signing input actually is, and let the engine refuse the mismatch.** `CKM_ECDSA` signs a **pre-computed digest**; pure Ed25519 (RFC 8032, `CKM_EDDSA` without the prehash parameter) signs the **message**. Handing a digest to Ed25519 signs the digest, not the transaction. The engine never hashes on your behalf — 1.x ran SHA-256 over whatever it received, so a caller passing an Ethereum Keccak-256 sighash got a perfectly valid signature over `sha256(keccak256(tx))`, which no verifier will accept. Digest length is checked exactly, because `CKM_ECDSA` *truncates* input longer than the base point order and silently signs a different value.
5. **Delegate to `C_Sign` and validate what comes back.** PKCS#11 returns ECDSA as raw `r||s`, each zero-padded to 32 bytes for secp256k1 — 64 bytes, **not** DER. A ~70–72 byte value means your binding already re-encoded it and must be decoded before use.
6. **Normalise secp256k1 to low-S before broadcast.** `(r, s)` and `(r, n − s)` are both valid, so a high-S signature is malleable: a third party can rewrite it and change the txid. Bitcoin has treated high-S as non-standard since Core 0.11.1 (BIP-146) and Ethereum rejects `s > secp256k1n/2` outright (EIP-2). PKCS#11 does not require the device to return low-S, so `enforce_low_s=True` (the default) flips it on the way out. Do **not** apply this to Ed25519 — RFC 8032 signatures are already canonical.
7. **Treat a failed `C_Sign` as ambiguous, never as "nothing happened".** A timeout can lose the response to an operation the device completed. `HsmSignerError` says so explicitly. Reconcile against the device's own log before retrying, exactly as you would a broker order — see `order-placement-idempotency`.
8. **Keep the denials.** Every outcome — `AUTHORIZATION_DENIED`, `KEY_NOT_FOUND`, `KEY_DISABLED`, `INPUT_DOMAIN_VIOLATION`, `SIGNER_FAILED`, `MALFORMED_SIGNATURE`, `EXPORT_ATTEMPT_REJECTED` — is written to the hash-chained log *before* the exception propagates. An auditor samples for refused and anomalous attempts; 1.x recorded only successes. Call `verify_audit_chain()` and ship records to append-only external storage (WORM bucket, SIEM), because a chain an attacker can rewrite wholesale is tamper-evident, not tamper-proof.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Simulating the HSM in application code.** Deriving key material from an alias, a config string, or any deterministic seed produces keys anyone can reconstruct. This is what 1.x of this skill did; it is the single most dangerous shortcut in the whole area.
- **Reading "non-exportable" as a single flag.** A key with `CKA_EXTRACTABLE=False` but `CKA_SENSITIVE=False` can have its private value read straight back with `C_GetAttributeValue` — no wrapping needed.
- **Trusting current attributes as lifetime evidence.** `CKA_NEVER_EXTRACTABLE` and `CKA_ALWAYS_SENSITIVE` are the only attributes that speak to the key's history. Without them, "extractable=False" only means nobody can wrap it *from now on*.
- **Re-hashing an already-hashed payload.** SHA-256 over a Keccak-256 sighash yields a valid signature over the wrong value. The chain rejects it, and the failure looks like a key problem rather than a domain problem.
- **Handing pure Ed25519 a digest.** It signs the 32 bytes you gave it. The signature verifies against those 32 bytes and against nothing the network cares about.
- **Passing a digest longer than the curve order to `CKM_ECDSA`.** It is truncated, not rejected — SHA-512 into a secp256k1 key signs the first 32 bytes.
- **Assuming the HSM returns DER, or that it returns low-S.** PKCS#11 specifies raw `r||s` and says nothing about S normalisation. Both assumptions produce transactions that are rejected downstream for reasons that look nothing like the cause.
- **Retrying a timed-out `C_Sign`.** The device may have signed already. Two signatures over two different nonces for the same input is not automatically harmful, but two *broadcast* transactions can be — reconcile first.
- **Claiming a FIPS level the certificate does not support.** "FIPS 140-2 Level 3" is now a sunsetting claim, and the level applies to the **module**, not to every service in it. AWS CloudHSM supports ed25519 only on `hsm2m.medium` in **non-FIPS mode** — a cluster-mode choice that is irreversible after creation.
- **Assuming an HSM makes the nonce safe.** ECDSA nonce generation happens inside the device and is not observable; a biased or repeated `k` leaks the private key. RFC 6979 deterministic derivation is the mitigation, but you cannot verify from outside that the device does it — treat it as a vendor question with a written answer.
- **Sharing one PKCS#11 session across trading threads without synchronisation.** Sessions are stateful; concurrent `C_SignInit`/`C_Sign` on one session interleaves. Use a session pool. The engine's own registry and audit chain are lock-guarded, but that does not make the vendor library thread-safe.
- **Logging only successes.** A rejected export attempt or a burst of `AUTHORIZATION_DENIED` from one identity is the earliest signal of a compromised caller, and it is exactly what an unlogged exception throws away.

## Verification

- Register a clean secp256k1 key with `fips_certification="FIPS_140_3_LEVEL_3"` and a certificate number, and confirm `audit_key_attributes` returns `[]`.
- Register the same alias twice and confirm `HsmKeyAlreadyRegisteredError` — 1.x silently overwrote the live key. Confirm `""` and `"   "` are rejected as aliases; 1.x accepted both.
- Register a key with `sensitive=False, extractable=False` and confirm a CRITICAL finding: non-extractable does not mean non-readable.
- Register a key with `never_extractable=False` and confirm a HIGH finding even though it is currently protected.
- Audit a `FIPS_140_2_LEVEL_3` key at `FIPS_140_2_PROGRAM_HISTORICAL_EPOCH` and confirm HIGH; audit it three months earlier and confirm MEDIUM. Supply CloudHSM `hsm1.medium`'s own 2026-01-04 date and confirm it goes HIGH months before the program-wide date.
- Call `attempt_export_private_key` and confirm it raises **and** appends an `EXPORT_ATTEMPT_REJECTED` record. Repeat with `extractable=True` and confirm it still raises — 1.x returned `None` (a silent pass) in exactly that case.
- Sign with a capturing signer and confirm it received the digest **verbatim** — 1.x re-hashed it.
- Feed a `RAW_MESSAGE` to a secp256k1 key, a digest to an Ed25519 key, a 31- and a 33-byte digest, and an unrecognised encoding string: each must raise and produce an `INPUT_DOMAIN_VIOLATION` record.
- Return a high-S signature from the signer and confirm the reported signature is normalised and `was_low_s_normalization_applied` is True. Confirm `n/2` itself is treated as low-S and `n/2 + 1` as high.
- Return a 71-byte DER blob and confirm `MALFORMED_SIGNATURE`. Raise `TimeoutError` from the signer and confirm the error text warns that the operation may still have completed.
- Request a signature as `AUDITOR`, then as `ADMIN`, and confirm both are denied by default and both appear in the log; construct the engine with `allowed_signing_roles=("OPERATOR", "ADMIN")` and confirm ADMIN then succeeds.
- `disable_key` a key, attempt to sign, and confirm `KEY_DISABLED`.
- Mutate or delete any record in `_audit_log` and confirm `verify_audit_chain()` returns False. Sign from 24 threads and confirm sequence numbers are `0..23` with no gaps.
- Run `python -m unittest discover -s skills/hardware-security-module-hsm-for-signing-keys/scripts` and confirm a 100% pass rate.

## Related Skills

- `crypto-wallet-key-custody-security`
- `hot-cold-wallet-split-for-trading-bots`
- `air-gapped-signing-workflow-for-cold-storage`
- `shamir-secret-sharing-for-key-backup`
- `key-rotation-schedule-for-hot-wallet-keys`
- `multi-signature-approval-for-large-transfers`
- `multi-party-computation-mpc-custody-solutions`
- `segregation-of-duties-for-custody-operations`
- `post-incident-forensics-for-suspected-key-compromise`
