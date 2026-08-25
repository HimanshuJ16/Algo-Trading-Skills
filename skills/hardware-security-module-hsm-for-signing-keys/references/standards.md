# Standards & Vendor Coverage — hardware-security-module-hsm-for-signing-keys

Primary sources (all consulted 2026-08-25):

- **OASIS**, *PKCS #11 Specification v3.1* (attributes, `C_Sign`, key objects): https://docs.oasis-open.org/pkcs11/pkcs11-spec/v3.1/os/pkcs11-spec-v3.1-os.html
- **OASIS**, *PKCS #11 Cryptographic Token Interface Current Mechanisms Specification v2.40* (CKM_ECDSA input, truncation, output encoding): https://docs.oasis-open.org/pkcs11/pkcs11-curr/v2.40/os/pkcs11-curr-v2.40-os.html
- **NIST CSRC**, *FIPS 140-3 Transition Effort*: https://csrc.nist.gov/projects/fips-140-3-transition-effort
- **NIST**, *FIPS 140-3, Security Requirements for Cryptographic Modules* (March 2019): https://csrc.nist.gov/pubs/fips/140-3/final
- **NIST SP 800-186**, *Recommendations for Discrete Logarithm-Based Cryptography: Elliptic Curve Domain Parameters* (February 2023): https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-186.pdf
- **AWS**, *Compliance validation for AWS CloudHSM*: https://docs.aws.amazon.com/cloudhsm/latest/userguide/fips-validation.html
- **AWS**, *Supported key types for the PKCS #11 library for AWS CloudHSM Client SDK 5*: https://docs.aws.amazon.com/cloudhsm/latest/userguide/pkcs11-key-types.html
- **AWS**, *AWS CloudHSM cluster modes*: https://docs.aws.amazon.com/cloudhsm/latest/userguide/cluster-hsm-types.html
- **Yubico**, *YubiHSM 2 FIPS 140-3 validation announcement*: https://www.yubico.com/press-releases/yubico-achieves-fips-140-3-validation-for-yubihsm-2-fips-strengthening-hardware-root-of-trust-for-critical-infrastructure/
- **BIP-146**, *Dealing with signature encoding malleability* (LOW_S rule): https://github.com/bitcoin/bips/blob/master/bip-0146.mediawiki
- **EIP-2**, *Homestead hard-fork changes* (`s > secp256k1n/2` invalid): https://eips.ethereum.org/EIPS/eip-2
- **RFC 6979**, *Deterministic Usage of DSA and ECDSA*: https://datatracker.ietf.org/doc/html/rfc6979
- **RFC 8032**, *Edwards-Curve Digital Signature Algorithm (EdDSA)*: https://datatracker.ietf.org/doc/html/rfc8032

## PKCS#11 Key Protection Attributes

Non-exportability is not one flag. Each attribute blocks a different operation, and the two read-only attributes are the only evidence about the key's history.

| Attribute | What TRUE/FALSE means | What it blocks | Modelled as |
|---|---|---|---|
| `CKA_SENSITIVE` | TRUE: the key's value cannot be revealed in plaintext | Reading the value with `C_GetAttributeValue` | `HsmKeyMetaData.sensitive` — FALSE is CRITICAL |
| `CKA_EXTRACTABLE` | FALSE: the key cannot be wrapped out | `C_WrapKey`. One-way: once FALSE it cannot be set back to TRUE | `HsmKeyMetaData.extractable` — TRUE is CRITICAL |
| `CKA_NEVER_EXTRACTABLE` | Read-only. TRUE only if `CKA_EXTRACTABLE` has been FALSE for the key's whole life | Nothing directly — it is the *attestation* that no wrapped copy was ever taken | `never_extractable` — FALSE is HIGH |
| `CKA_ALWAYS_SENSITIVE` | Read-only. TRUE only if `CKA_SENSITIVE` has been TRUE for the key's whole life | Nothing directly — attests the value was never readable | `always_sensitive` — FALSE is HIGH |
| `CKA_SIGN` | TRUE: the key may be used with `C_SignInit`/`C_Sign` | — | Not modelled; enforce at the device |

A key that is *currently* non-extractable but has `CKA_NEVER_EXTRACTABLE=False` must be treated as already exposed: the current attribute values say nothing about copies taken earlier. Because `CKA_EXTRACTABLE` is one-way, that state cannot be repaired — only replaced.

## Signing Input and Output Semantics

| Mechanism | Input | Output | Consequence |
|---|---|---|---|
| `CKM_ECDSA` ("ECDSA without hashing") | A **pre-computed hash**. The mechanism "does not compute a hash value on the message". Input longer than the base point order **is truncated** to the order's byte length | "r and s, each of which is zero-padded to the byte length of the base point order and concatenated together" — 64 bytes for secp256k1 | Passing a raw message signs the message bytes as if they were a digest. Passing SHA-512 to a secp256k1 key silently signs the first 32 bytes. Expecting DER back yields a length mismatch |
| `CKM_EDDSA` (pure, RFC 8032) | The **message**, not a digest | R‖S, 64 bytes | Passing a digest produces a signature over the digest, which no verifier of the transaction will accept |
| `CKM_SHA256_HMAC` | Message or digest — an opaque byte string | 32-byte tag | Symmetric; no malleability or domain concern |

The engine models these as `ALLOWED_INPUT_ENCODINGS` and `EXPECTED_SIGNATURE_LENGTHS`, and refuses any combination outside them.

**Nonce generation is not observable from outside the device.** RFC 6979 warns that "even slight biases in that process may be turned into attacks on the signature schemes" and specifies deterministic `k` derived from the private key and message hash via HMAC-DRBG. Whether a given HSM does this is a vendor question; the module cannot check it and does not claim to.

## secp256k1 Signature Malleability

| Rule | Requirement | Status | Source |
|---|---|---|---|
| Bitcoin LOW_S | "We require that the S value inside ECDSA signatures is at most the curve order divided by 2." Upper bound `0x7FFFFFFF…681B20A0` | Relay/standardness policy in Bitcoin Core since **v0.11.1**; BIP-146 proposed it as consensus via BIP9 | BIP-146 |
| Ethereum | "All transaction signatures whose s-value is greater than `secp256k1n/2` are now considered invalid." The `ecrecover` precompile still accepts high-S for legacy compatibility | **Consensus** since the Homestead hard fork | EIP-2 |
| PKCS#11 | Says nothing about S normalisation | — | OASIS pkcs11-curr v2.40 |

`SECP256K1_ORDER // 2` in this module is cross-checked in the test suite against the literal bound published in BIP-146, so the curve constant is verified against a source outside this repository. Ed25519 is **not** normalised: RFC 8032 signatures are canonical by construction.

## FIPS 140 Validation Currency

| Milestone | Date | Source |
|---|---|---|
| CMVP began validating to FIPS 140-3 | 2020-09-22 | NIST FIPS 140-3 Transition Effort |
| CMVP stopped accepting new FIPS 140-2 submissions | **2022-04-01** | NIST FIPS 140-3 Transition Effort |
| All remaining FIPS 140-2 certificates placed on the Historical List | **2026-09-22** (individual certificates expire five years after validation, so many move earlier) | NIST FIPS 140-3 Transition Effort |
| Historical List status | "Even on the historical list, CMVP supports the purchase and use of these modules for existing systems" — a migration finding, not an outage | NIST FIPS 140-3 Transition Effort |

FIPS 140-3 references **ISO/IEC 19790:2012** for module requirements and **ISO/IEC 24759:2017** for testing. The four security levels and what Level 3 and Level 4 add are defined there; this skill does not restate them, and no claim in this module depends on a specific level beyond recording which one the certificate carries.

**The validation applies to the module, not to a specific service inside it.** A "FIPS 140-3 Level 3" HSM operated in a non-FIPS cluster mode, or invoking a mechanism outside its approved mode, is not producing FIPS-validated signatures — see the CloudHSM row below.

`FIPS_140_2_PROGRAM_HISTORICAL_EPOCH = 1_790_035_200.0` is 2026-09-22T00:00:00Z. It is only a **backstop**: pass the module's own `fips_historical_epoch` from its CMVP entry whenever you know it.

## Vendor Coverage

| Vendor / module | Validation | Curves and mechanisms relevant here | Notes |
|---|---|---|---|
| AWS CloudHSM `hsm2m.medium` | **FIPS 140-3 Level 3, certificate #4703** | EC: secp224r1, secp256r1, **secp256k1**, secp384r1, secp521r1, ed25519; RSA; AES; ML-DSA | ed25519 is **only supported on hsm2m.medium in non-FIPS mode**. Cluster mode (FIPS vs non-FIPS) **cannot be changed after creation** |
| AWS CloudHSM `hsm1.medium` | FIPS 140-2 Level 3, certificate #4218 — **moved to the Historical List on 2026-01-04** | As above, minus ed25519 and ML-DSA | AWS recommends migrating to hsm2m.medium. Backups are not portable between FIPS and non-FIPS clusters |
| YubiHSM 2 FIPS | **FIPS 140-3 Level 3, certificate #5302** (previously FIPS 140-2 Level 3, certificate #3916) | secp224r1, secp256r1, secp256k1, secp384r1, secp521r1, brainpool curves, Ed25519 | In approved mode only FIPS-approved algorithms are available; non-approved algorithms require the non-approved mode |
| Thales Luna / Entrust nShield / custodian PKCS#11 endpoints | *Not verified here* | — | Check the module's own CMVP certificate; do not assume a vendor family shares one validation |

## Is secp256k1 FIPS-approved?

Yes, as an *allowed* curve — with a specific scope. NIST SP 800-186 Appendix H.2 states: "This standard also allows the curve secp256k1 specified in SEC 2 … The curve secp256k1 is allowed to be used for blockchain-related applications." It is not among the recommended curves in the main body; it sits alongside the Brainpool curves in the "Other Allowed Elliptic Curves" appendix. Ed25519 (edwards25519) *is* specified in the main body for EdDSA.

So the constraint on ed25519 in AWS CloudHSM is a **vendor implementation limit**, not a NIST one. Confirm mechanism availability against your own module's security policy rather than against the standard.

## Category

`Crypto Custody & Security` / `Hardware Security Modules (HSM) & PKCS#11 Key Isolation` — see the top-level `mappings/` directory for how this category rolls up across the full skill library.

## Regulatory & Operational Notes

FIPS 140-3 validation is mandatory for US federal use of cryptographic modules and is commonly adopted contractually by institutional counterparties, insurers and SOC 2 auditors; it is **not** itself a securities or digital-asset regulation. Jurisdiction-specific custody obligations (qualified custodian rules, licensing) are out of scope here — see `regulatory-custody-requirements-by-jurisdiction`. The audit chain in this module supports evidence retention for a control review; retention periods themselves are jurisdictional — see `record-retention-periods-by-jurisdiction`.

**Not verified:** whether any specific HSM performs RFC 6979 deterministic nonce derivation. Vendor documentation consulted here does not state it, and it cannot be observed from outside the module. This skill treats it as a written question for the vendor, not as an assumption.
