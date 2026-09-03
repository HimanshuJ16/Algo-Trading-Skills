# Workflows for Air-Gapped Signing in Cold Storage

## Trust model

The online coordinator is internet-connected and therefore assumed compromisable.
Every control that must survive its compromise lives on the offline side:

| Control | Enforced by | Survives a compromised coordinator? |
|---|---|---|
| Destination / amount / chain shown to a human | Vault (`clear_signing_display`) | Yes — derived from the bytes being signed |
| Chain authorisation | Vault (`expected_chain_id`) | Yes |
| Amount ceiling, destination allowlist | Vault | Yes |
| Refusal to re-sign an intent | Vault (`_signed_hashes`, monotonic nonce) | Yes |
| Intent provenance (`_issued`) | Coordinator | **No** — defends operator error only |
| Single-submission ledger (`_consumed`) | Coordinator | **No** |
| Signature verification | Coordinator | **No** — and the HMAC key is symmetric anyway |

Read the bottom three rows as defence in depth. If the coordinator is the thing
that is owned, the vault display and the human reading it are the last control.

## Institutional treasury transfer pipeline

1. **Create the intent.** `create_unsigned_transfer(address, amount)` validates
   the address, canonicalises the amount, and only then commits the next nonce —
   validation precedes the side effect, so a typo does not leave a permanent gap
   in the account's nonce sequence. The intent is stored under its payload hash.
2. **Export.** `to_qr_code_data()` produces sorted-key, separator-tight,
   ASCII-safe JSON (a realistic intent is well under 500 bytes, comfortably
   inside QR capacity). Move it on a QR code or a clean, inspected SD card,
   record media custody, and permit no USB, Bluetooth, Wi-Fi, or cellular bridge.
3. **Inspect the medium.** Verify provenance and scan the media before it touches
   the offline host. Quarantine anything unexpected rather than loading it.
4. **Decode strictly.** The vault rejects malformed JSON, a field set that is not
   an exact match, a version-1 payload carrying no chain id, an amount that is
   non-finite, non-positive, over 18 decimals, or outside the uint256 wei range,
   and a `bool` masquerading as a nonce.
5. **Vault policy, then replay checks, then the human.** Chain, allowlist, and
   ceiling are machine-decidable and are refused without prompting. The vault then
   refuses any payload hash it has already signed, and by default any nonce at or
   below the highest it has signed. Only a payload that survives all of this is
   shown to a person.
6. **Clear-sign.** `clear_signing_display()` renders destination, amount, chain
   id, nonce, and payload SHA-256 from the payload about to be signed. The
   approver compares it against the instruction received over a separate channel
   — CCSS v9 1.05.8.1 asks for exactly this — and returns `True` or refuses.
   Anything other than `True`, including an exception, is a refusal.
7. **Return.** The vault emits a `SignedPayload` bound to the canonical payload
   and its hash. `to_transport_data()` serialises it for the return trip;
   `from_transport_data()` validates every field's type and shape on arrival, so
   hostile media fails closed instead of raising out of a later comparison.
8. **Verify.** The coordinator recomputes the payload hash and checks, in order:
   hash binding, that it issued this intent, chain match, not invalidated, known
   signer key id, not already consumed, and finally the signature under
   `hmac.compare_digest`. Any failure is `REJECTED` and nothing is dispatched.
9. **Submit once.** The payload is added to `_consumed` **before** the adapter is
   called. This ordering is the point: a process that dies between the two leaves
   a payload that can never be resubmitted, which is the safe direction to fail.
10. **Classify the outcome honestly.** An adapter returning a reference string is
    `ACCEPTED`. An adapter that raises, or returns nothing usable, is
    `UNRESOLVED` — never `REJECTED`. `BroadcastResult` refuses `bool()`
    conversion so no call site can quietly file the ambiguous case as failure.

## Failure and recovery boundaries

- **Malformed media or unknown payload** — quarantine the medium, reject the
  payload, investigate, then issue a fresh intent. Do not re-export the old one.
- **Destination, amount, chain, nonce, hash, or signer mismatch** — do not sign
  and do not broadcast. Escalate to dual-control review; a mismatch is a tamper
  signal, not a formatting problem.
- **Lost or damaged media** — call `invalidate_intent(payload_hash)`. The nonce
  is *not* reused: the replacement intent takes a fresh one, so a recovered copy
  of the old media can never be broadcast. `invalidate_intent` returns `False`
  for an intent that was never issued or has already been submitted.
- **Ambiguous broadcast** — the hash sits in `unresolved_payload_hashes`. Query
  the chain or the node for authoritative evidence, then call
  `resolve_unresolved(payload_hash, landed_on_chain)`. Reconciling does **not**
  re-open the payload for submission: if the transfer genuinely never landed, the
  remedy is a new intent with a new nonce, never a resubmission of the old
  envelope. Never assume failure because a response was lost.
- **Approver unsure** — refuse. A refusal costs one nonce; an approval of a
  transfer nobody understands costs the balance.
- **Suspected key or device compromise** — stop signing, preserve the device and
  media as evidence, invoke incident response, and rotate or quarantine keys under
  the custody policy. See `post-incident-forensics-for-suspected-key-compromise`
  and `recovery-plan-for-lost-or-compromised-keys`.

## Taking this to production

The module is a model of the boundary, not a deployable vault. Before it protects
funds:

- Replace the HMAC seam with audited chain-native secp256k1 signing inside a
  hardware wallet or HSM, so the online side holds only a public key. The current
  `verification_key` can forge signatures.
- Sign the real RLP-encoded transaction body, including gas parameters and any
  `data` field, and render the display from *that* — not from a separate intent
  object that the chain will never see.
- Make `_issued`, `_consumed`, `_invalidated`, `_unresolved`, and the vault's
  `_signed_hashes` durable and restart-safe, and serialise access if more than one
  coordinator process can run.
- Add out-of-band destination confirmation against an address book the
  coordinator cannot edit.
- Separate the duties: intent creation, media handling, offline approval, and
  broadcast reconciliation should not all be one person.
