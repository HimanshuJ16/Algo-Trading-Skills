# Pre-Flight / Sign-off Checklist — hardware-security-module-hsm-for-signing-keys

Use this before a key signs anything that moves real value.

## Key Provisioning

- [ ] **Generated on-device:** Confirm the key was created with `C_GenerateKeyPair` inside the HSM and never imported from software. An imported key was in host memory by definition.
- [ ] **No simulated key material anywhere:** Confirm no code path derives a "private key" from an alias, config string, or any deterministic seed. (Pre-2.0 versions of this skill's helper did exactly that — those keys were reconstructible from the alias alone.)
- [ ] **Attributes read back, not asserted:** Confirm `CKA_SENSITIVE`, `CKA_EXTRACTABLE`, `CKA_NEVER_EXTRACTABLE` and `CKA_ALWAYS_SENSITIVE` came from `C_GetAttributeValue`, not from a config file.
- [ ] **Both protections set:** `CKA_SENSITIVE=True` **and** `CKA_EXTRACTABLE=False`. Non-extractable does not mean non-readable.
- [ ] **Lifetime evidence clean:** `CKA_NEVER_EXTRACTABLE=True` and `CKA_ALWAYS_SENSITIVE=True`. If either is False, the key is exposed material — rotate rather than reconfigure; `CKA_EXTRACTABLE` is one-way.
- [ ] **Alias collision impossible:** Confirm `register_hardware_key` raises on a duplicate alias, and that no operational script relies on re-registering to update metadata.

## Validation Currency

- [ ] **CMVP certificate recorded:** Confirm the certificate *number* is stored, not just a level string, and that it matches the CMVP validated-modules list.
- [ ] **FIPS 140-3, not 140-2:** Confirm the module is FIPS 140-3 validated (AWS CloudHSM hsm2m.medium #4703, YubiHSM 2 FIPS #5302). CMVP stopped accepting FIPS 140-2 submissions on 2022-04-01.
- [ ] **Per-certificate sunset date supplied:** Confirm `fips_historical_epoch` is set from the module's own CMVP entry — individual certificates go historical five years after validation, ahead of the program-wide 2026-09-22 date (CloudHSM hsm1.medium #4218 went historical 2026-01-04).
- [ ] **Cluster/operating mode checked:** Confirm the module is running in its FIPS-approved mode if you are claiming FIPS-validated signing. AWS CloudHSM supports ed25519 **only in non-FIPS mode**, and cluster mode cannot be changed after creation.
- [ ] **Audit is clean:** Run `audit_key_attributes` and confirm it returns `[]`, or that every finding has a recorded, accepted remediation.

## Signing Input Domain

- [ ] **Nothing re-hashes:** Confirm no layer hashes a value that is already a digest. Sign with a capturing signer and assert it received the exact bytes you computed.
- [ ] **Encoding declared correctly:** Ethereum → `KECCAK256_DIGEST`; Bitcoin → `SHA256D_DIGEST`; pure Ed25519 → `RAW_MESSAGE` (the message, **not** a digest).
- [ ] **Digest length exact:** Confirm a 31- and a 33-byte digest are both rejected. `CKM_ECDSA` truncates over-long input and silently signs a different value.
- [ ] **Unknown encodings fail closed:** Confirm an unrecognised `input_encoding` raises rather than defaulting to anything.

## Signature Handling

- [ ] **Encoding understood:** Confirm the binding returns raw `r||s` (64 bytes for secp256k1), not DER. A ~70–72 byte value must be decoded first.
- [ ] **Low-S enforced:** Confirm `enforce_low_s=True` and that a device-returned high-S signature comes back normalised. Ethereum rejects `s > secp256k1n/2` at consensus; Bitcoin treats high-S as non-standard.
- [ ] **Low-S not applied to Ed25519:** Confirm normalisation runs only for `SECP256K1`. RFC 8032 signatures are already canonical.
- [ ] **No validity claim you cannot back:** Confirm nothing downstream reads a "signature valid" flag from this module — it checks length and range only. Verify against the public key with your chain library before broadcast.
- [ ] **Nonce question answered in writing:** Confirm you have the vendor's statement on ECDSA nonce generation (RFC 6979 deterministic, or the RNG used). It cannot be observed from outside the module.

## Authorisation & Concurrency

- [ ] **Segregation of duties:** Confirm the role that administers key attributes is not in `allowed_signing_roles`. `ADMIN` is excluded by default; any override is a recorded policy decision.
- [ ] **Real quorum lives elsewhere:** Confirm value thresholds and multi-party approval are enforced in the HSM/custodian policy engine, not only in this process.
- [ ] **Session pooling:** Confirm concurrent trading threads do not share one PKCS#11 session. Sessions are stateful and interleaved `C_SignInit`/`C_Sign` pairs corrupt each other.
- [ ] **Chain integrity under load:** Sign concurrently and confirm audit sequence numbers are gap-free and `verify_audit_chain()` holds.

## Failure Handling

- [ ] **Timeout is ambiguous, not negative:** Confirm no code retries a failed `C_Sign` without reconciling against the device's own log. A lost response can hide a completed signature.
- [ ] **Kill path exists:** Confirm `disable_key` blocks further signing, **and** that the object is destroyed or `CKA_SIGN` cleared at the device — disabling in this engine does not touch the HSM.

## Audit Trail

- [ ] **Denials recorded:** Confirm `AUTHORIZATION_DENIED`, `KEY_NOT_FOUND`, `KEY_DISABLED`, `INPUT_DOMAIN_VIOLATION`, `SIGNER_FAILED`, `MALFORMED_SIGNATURE` and `EXPORT_ATTEMPT_REJECTED` all produce records, not just exceptions.
- [ ] **Export attempts rejected and logged:** Confirm `attempt_export_private_key` raises for every key, including one with `CKA_EXTRACTABLE=True`.
- [ ] **Chain verified:** Confirm `verify_audit_chain()` returns True, and that mutating or deleting a record makes it return False.
- [ ] **Shipped off-box:** Confirm records reach append-only external storage (WORM/object-lock bucket, SIEM) continuously. In-process the chain is tamper-**evident**, not tamper-proof.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/hardware-security-module-hsm-for-signing-keys/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- HSM model / CMVP certificate: ___________________________
- Environment tested (sandbox/production cluster): ___________________________
