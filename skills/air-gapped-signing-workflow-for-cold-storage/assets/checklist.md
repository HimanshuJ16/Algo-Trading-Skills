# Checklist for Air-Gapped Signing in Cold Storage

Derived from the Verification section of `SKILL.md`. Every box is checkable
against a test or an observable behaviour.

## Clear signing and approval

- [ ] An `approval_callback` is wired; a vault without one has been confirmed to
      refuse a well-formed payload.
- [ ] The display handed to the approver is derived from the payload that will be
      signed, and its payload hash equals `signed.original_payload_hash`.
- [ ] Destination, amount, chain id, and nonce all appear in the display.
- [ ] Approvers returning `False`, a truthy non-`True` value, `None`, or raising
      are all confirmed to refuse.
- [ ] The approver compares the display against an instruction received over a
      separate channel, against an address book the coordinator cannot edit
      (CCSS v9 1.05.8.1).
- [ ] Machine-refusable cases (wrong chain, off-allowlist, over-ceiling) are
      denied *without* prompting a human.

## Payload and chain binding

- [ ] `chain_id` is mandatory, matches the vault's `expected_chain_id`, and
      changes the payload hash (EIP-155).
- [ ] Version-1 payloads carrying no chain id are rejected, not upgraded.
- [ ] Serialisation is canonical (sorted keys, tight separators, ASCII) and the
      hash is SHA-256 over exactly those bytes.
- [ ] The decoder requires an exact field set — missing *and* unknown fields are
      rejected.
- [ ] Amounts are positive, finite, at most 18 decimals, and within the uint256
      wei range; `0`, `-1`, `NaN`, `Infinity`, 19 decimals, `2**256`, and
      `1E+1000` all raise.
- [ ] A `bool` is rejected wherever an integer nonce or chain id is expected.

## Key isolation

- [ ] The private key exists only inside `OfflineAirGappedSigner`; the signing
      host has no Wi-Fi, Bluetooth, or cellular modem.
- [ ] The coordinator never holds the vault private key.
- [ ] It is understood and documented that the reference HMAC primitive is
      **symmetric** — the coordinator's `verification_key` can forge any
      signature — and that production requires audited secp256k1 in a hardware
      wallet or HSM with only a public key online.

## Replay and single submission

- [ ] The vault refuses to re-sign a payload hash it has already signed.
- [ ] Monotonic nonce enforcement is on, or its relaxation is a documented
      decision.
- [ ] The coordinator commits a nonce only after the intent validates — a
      rejected address leaves no nonce gap.
- [ ] The payload is recorded as consumed *before* the broadcast adapter is
      called.
- [ ] A replayed envelope is rejected without reaching the RPC layer.
- [ ] `invalidate_intent` voids a lost-media intent, and the replacement takes a
      fresh nonce.

## Ambiguous outcomes

- [ ] An adapter that raises yields `UNRESOLVED`, never `REJECTED`.
- [ ] An adapter returning `""`, `None`, or a non-string yields `UNRESOLVED`.
- [ ] `bool(BroadcastResult(...))` raises, so no call site can coerce the
      three-state outcome into two.
- [ ] Unresolved hashes are reconciled against chain evidence via
      `resolve_unresolved`, and a transfer that never landed is retried as a
      **new intent with a new nonce**, never as a resubmitted envelope.
- [ ] A reconciliation runbook exists, and no code path retries a broadcast in an
      unbounded loop.

## Hostile media

- [ ] Envelope fields are type- and shape-validated at construction: a non-string
      signature, a short signature, an uppercase hash, and a blank signer id all
      raise `AirGapSigningError` rather than failing later.
- [ ] Passing a non-envelope object to `broadcast_to_network` fails closed.
- [ ] Tampered signatures, rebound payloads, foreign signers, and never-issued
      intents are all rejected and never reach the adapter.
- [ ] QR/SD media is inspected and tracked through chain of custody; USB,
      Bluetooth, Wi-Fi, and cellular bridges are prohibited.

## Production readiness

- [ ] `_issued`, `_consumed`, `_invalidated`, `_unresolved`, and the vault's
      `_signed_hashes` are durable and restart-safe.
- [ ] Concurrent coordinator processes are serialised, or ruled out.
- [ ] Signing happens over the real RLP-encoded transaction body, and the display
      is rendered from that body — not from a separate intent object.
- [ ] Intent creation, media handling, offline approval, and broadcast
      reconciliation have separation of duties.
- [ ] Key/device compromise, malformed media, and mismatched payloads each have
      an incident escalation path.
- [ ] `python -m unittest discover -s skills/air-gapped-signing-workflow-for-cold-storage/scripts`
      passes 100%.

## Sign-off

- Security Architect: ___________________________
- Custody Operations: ___________________________
- Date: ___________________________
