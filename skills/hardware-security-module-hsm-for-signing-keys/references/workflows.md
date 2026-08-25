# Workflows — hardware-security-module-hsm-for-signing-keys

Full procedure behind the summary in `SKILL.md`. Citations are in
`references/standards.md`.

Throughout: `HsmSigningManagerEngine` is a **policy and audit layer**. It holds
no key material and computes no signatures. Every step below assumes a real
PKCS#11 binding supplies the `signer` callable.

## 1. Provision the key on the device

1. Open a PKCS#11 session against the token and authenticate the PIN. Do not
   share one session across trading threads: sessions are stateful and
   concurrent `C_SignInit`/`C_Sign` pairs on a single session interleave. Use a
   session pool sized to your concurrency.
2. Generate on-device with `C_GenerateKeyPair`, setting at minimum:
   - `CKA_SENSITIVE = True`
   - `CKA_EXTRACTABLE = False`
   - `CKA_SIGN = True`
   Never generate in software and import. An imported key was, by definition,
   in host memory.
3. Read the attributes back with `C_GetAttributeValue` — including the two
   read-only ones, `CKA_NEVER_EXTRACTABLE` and `CKA_ALWAYS_SENSITIVE`. These
   are the evidence; the values you *intended* to set are not.
4. Record the module's CMVP certificate number, and its Historical List date if
   it is a FIPS 140-2 certificate.

## 2. Register with the engine

```python
meta = engine.register_hardware_key(
    key_alias="CUSTODY_HOT_01",
    algorithm="SECP256K1",
    slot_id=0,
    sensitive=attrs[CKA_SENSITIVE],
    extractable=attrs[CKA_EXTRACTABLE],
    never_extractable=attrs[CKA_NEVER_EXTRACTABLE],
    always_sensitive=attrs[CKA_ALWAYS_SENSITIVE],
    fips_certification="FIPS_140_3_LEVEL_3",
    fips_certificate_number="4703",
)
```

Registration is **not** idempotent-by-overwrite. A duplicate alias raises
`HsmKeyAlreadyRegisteredError`. This mirrors the device: generating over an
existing label destroys the only key that can sign for addresses already
derived from it.

## 3. Audit the attributes and the validation

```python
findings = engine.audit_key_attributes("CUSTODY_HOT_01", current_time_epoch=now)
```

Findings are returned worst-first. Interpretation:

| Finding | Level | Meaning and action |
|---|---|---|
| `CKA_EXTRACTABLE` is True | CRITICAL | The key can be wrapped out with `C_WrapKey`. Not repairable — the attribute is one-way. Generate fresh material and migrate balances |
| `CKA_SENSITIVE` is False | CRITICAL | The value can be read with `C_GetAttributeValue` without wrapping anything. Regenerate |
| `CKA_NEVER_EXTRACTABLE` is False | HIGH | A wrapped copy may already exist outside the HSM. Treat as exposed and rotate |
| `CKA_ALWAYS_SENSITIVE` is False | HIGH | The value was readable at some point. Treat as exposed and rotate |
| FIPS 140-2 on/after its historical date | HIGH | Migration finding. CMVP still supports historical modules for existing systems, so this is not an outage |
| FIPS 140-2 before its historical date | MEDIUM | Plan the FIPS 140-3 migration now; certificates sunset five years after validation, ahead of the 2026-09-22 program date |
| No CMVP certificate number recorded | LOW | The FIPS claim is unauditable. Record the number |

Pass the module's own `fips_historical_epoch` when you know it. The engine
falls back to the program-wide backstop only in its absence, and that backstop
is *later* than many individual certificates — AWS CloudHSM `hsm1.medium`
(#4218) went historical on 2026-01-04.

## 4. Build the signing input in the right domain

The engine never hashes on your behalf, and never transforms the bytes. You
declare what they are and it validates the declaration.

| Chain / use | Compute | `input_encoding` | Algorithm |
|---|---|---|---|
| Ethereum / EVM transaction | Keccak-256 of the RLP-encoded signing payload | `KECCAK256_DIGEST` | `SECP256K1` |
| Bitcoin transaction | Double SHA-256 sighash | `SHA256D_DIGEST` | `SECP256K1` |
| Generic payload over ECDSA | SHA-256 | `SHA256_DIGEST` | `SECP256K1` |
| Ed25519 (pure, RFC 8032) | **Nothing — pass the message** | `RAW_MESSAGE` | `ED25519` |
| HMAC-SHA256 (internal MAC) | Either | any | `HMAC_SHA256` |

Two failure modes this prevents:

- **Re-hashing.** A digest passed to something that hashes again produces a
  valid signature over the wrong value. Pre-2.0 versions of this module did
  exactly that.
- **Domain swap.** `CKM_ECDSA` signs a digest; pure Ed25519 signs a message.
  Passing a digest to Ed25519 signs the digest. The signature is
  cryptographically fine and useless.

Digest length is checked exactly (32 bytes), because `CKM_ECDSA` **truncates**
input longer than the base point order rather than rejecting it.

## 5. Sign

```python
def pkcs11_signer(meta, signing_input: bytes) -> bytes:
    with session_pool.acquire() as session:
        key = session.get_key(label=meta.key_alias, object_class=PRIVATE_KEY)
        return key.sign(signing_input, mechanism=Mechanism.ECDSA)

report = engine.sign_transaction_payload(
    HsmSignatureRequest(
        key_alias="CUSTODY_HOT_01",
        signing_input=keccak256(rlp_payload),
        input_encoding="KECCAK256_DIGEST",
        caller_identity="algo_execution_bot_01",
        caller_role="OPERATOR",
        request_id=client_order_id,
    ),
    signer=pkcs11_signer,
    current_time_epoch=now,
)
```

Order of enforcement, each writing an audit record before raising:

1. Key exists → `KEY_NOT_FOUND`
2. Key not disabled → `KEY_DISABLED`
3. Role authorised → `AUTHORIZATION_DENIED`
4. Encoding valid for the algorithm, and digest length exact →
   `INPUT_DOMAIN_VIOLATION`
5. Signer succeeds → `SIGNER_FAILED`
6. Signature length matches the algorithm, and `r`/`s` are in range →
   `MALFORMED_SIGNATURE`
7. secp256k1 low-S normalisation → `SIGNATURE_SUCCESS`

### Role authorisation and segregation of duties

`DEFAULT_SIGNING_ROLES = ("OPERATOR",)`. `ADMIN` is excluded by default: a role
that both administers key attributes and authorises transfers has no
segregation of duties. This is an engineering policy default, **not** a
regulatory requirement — override it explicitly with
`allowed_signing_roles=("OPERATOR", "ADMIN")` if your mandate says so, and
record that decision. Real quorum enforcement belongs in the HSM's own policy
engine, not in a process the trading system controls; see
`multi-signature-approval-for-large-transfers` and
`segregation-of-duties-for-custody-operations`.

### Signature encoding

PKCS#11 returns ECDSA as raw `r||s`, each zero-padded to the order's byte
length — 64 bytes for secp256k1. It is **not** DER. If your binding hands back
~70–72 bytes it has already re-encoded, and you must decode before use;
the engine rejects it as `MALFORMED_SIGNATURE` rather than passing an
unparseable value to a broadcast path.

### Low-S normalisation

`(r, s)` and `(r, n − s)` are both valid ECDSA signatures. Bitcoin has treated
high-S as non-standard since Core 0.11.1 (BIP-146); Ethereum rejects
`s > secp256k1n/2` at consensus (EIP-2). PKCS#11 does not require the device to
return low-S, so `enforce_low_s=True` (the default) flips it on the way out and
reports `was_low_s_normalization_applied`.

Never apply this to Ed25519 — RFC 8032 signatures are canonical by
construction, and "normalising" one would corrupt it. The engine applies it
only to `SECP256K1`.

## 6. Handle a failed or ambiguous signing call

`HsmSignerError` is raised when the signer throws. Its message states
explicitly that a failure is **not** proof that nothing was signed: a timeout
can lose the response to an operation the device completed.

Procedure:

1. Do **not** retry immediately.
2. Reconcile against the HSM's own audit log / the vendor's operation counters
   for that key.
3. If the device signed, decide at the transaction layer whether that signature
   was broadcast. Re-signing the same input yields a *different* signature
   (different nonce) but authorises the same transfer — two broadcasts of two
   distinct valid transactions is the failure to avoid.
4. Only then retry, carrying the same `request_id` so the two attempts are
   correlated in the log.

This mirrors `order-placement-idempotency`: an ambiguous outcome is resolved by
reconciliation, never by a blind retry loop.

## 7. Reject export attempts, loudly

```python
engine.attempt_export_private_key("CUSTODY_HOT_01", current_time_epoch=now)
# always raises HsmPolicyViolationError, after writing EXPORT_ATTEMPT_REJECTED
```

It raises even when the key's attributes say it *is* extractable: a request to
pull private key material into application memory is a policy failure
regardless of whether the hardware would permit it, and in that case the
`CKA_EXTRACTABLE=True` state is itself a CRITICAL finding.

## 8. Retire a key

```python
engine.disable_key("CUSTODY_HOT_01", "suspected compromise", current_time_epoch=now)
```

Subsequent signing attempts fail with `KEY_DISABLED` and are recorded. Disabling
in this engine stops *your* process; it does not touch the device. Destroy or
deactivate the object at the HSM (`C_DestroyObject`, or clear `CKA_SIGN`) as
well, and see `key-rotation-schedule-for-hot-wallet-keys` and
`recovery-plan-for-lost-or-compromised-keys`.

## 9. Preserve the audit trail

Every record carries `previous_record_hash` and a `record_hash` computed over
its own content plus that link, so altering or deleting any earlier entry
invalidates every later one:

```python
assert engine.verify_audit_chain()
for record in engine.audit_log:
    ship_to_worm_storage(record)
```

This is tamper-**evident**, not tamper-proof: an attacker who can rewrite the
whole in-memory list can recompute the chain. Tamper *resistance* requires
append-only external storage — a WORM bucket, an object-lock S3 bucket, or a
SIEM the trading process cannot delete from. Ship records continuously rather
than at shutdown, and see `structured-logging-for-post-incident-forensics`.

Records include denials, which is the point: a rejected export attempt or a
burst of `AUTHORIZATION_DENIED` from one identity is the earliest signal of a
compromised caller.

## 10. What this workflow does not cover

- **Nonce quality.** ECDSA `k` generation happens inside the device and cannot
  be observed. RFC 6979 deterministic derivation is the mitigation; get the
  vendor's answer in writing.
- **Signature verification.** Confirming a signature against a public key needs
  curve arithmetic this dependency-free module does not implement. Verify at
  the transaction layer with your chain library before broadcast.
- **Transfer approval quorums and value limits.** Those belong in the HSM or
  custodian policy engine.
- **Physical and personnel controls** around the device and its PINs — see
  `segregation-of-duties-for-custody-operations` and
  `employee-offboarding-procedure-for-custody-access`.
