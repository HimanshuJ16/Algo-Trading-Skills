---
name: air-gapped-signing-workflow-for-cold-storage
description: >-
  Air-gapped signing workflow for Ethereum-style cold storage. An online
  coordinator issues chain-bound transfer intents that cross a QR/SD gap to an
  offline vault, which re-derives the approval display from the exact bytes it
  will sign, applies its own policy and anti-replay ledger, and returns an
  envelope bound to the payload hash. Broadcast is single-shot and reports an
  ambiguous RPC outcome as unresolved rather than failed.
domain: Crypto Custody & Security
subdomain: Air-Gapped Signing & Key Isolation
tags: ["crypto-custody", "air-gap", "cold-storage", "clear-signing", "blind-signing", "eip-155", "replay-protection", "transaction-signing"]
brokers_frameworks: ["Generic EVM RPC", "Hardware Wallet (Ledger/Trezor)", "HSM", "CCSS v9", "EIP-155", "ERC-7730", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a treasury process, withdrawal pipeline, or trading-bot
funding job must move crypto out of cold storage, and the signing key must never
exist on an internet-connected machine. It models the full round trip —
coordinator issues an intent, the intent crosses an air gap on QR or SD media, an
offline vault displays and signs it, the envelope comes back, the coordinator
verifies the binding and submits it exactly once.

The controls it encodes are the ones that survive a compromised online host:
the vault derives what the human sees from the exact bytes it is about to sign,
enforces its own policy and its own replay ledger, and the intent is bound to an
EIP-155 chain id rather than to a network label.

It exists because the expensive failures in this workflow are not broken
cryptography. They are a signer approving a screen that described a different
transaction, and an operator retrying a broadcast whose response was merely lost.

## When NOT to Use

- **As a transaction builder.** `UnsignedPayload` is a transfer *intent*, not a
  signable Ethereum transaction. It carries no gas parameters, no `data` field,
  and is never RLP-encoded. Real signing must happen over the actual transaction
  body, or the approver has reviewed something the chain will never see.
- **As cryptography.** The signature primitive here is a keyed HMAC, which is
  **symmetric**: the `verification_key` the online coordinator holds is enough to
  forge any signature. That is the inverse of what custody requires, and is
  tolerable only because this module never touches funds. Production signs with
  audited secp256k1 inside a hardware wallet or HSM, and the online side holds
  only a public key.
- **For contract calls or token transfers.** The display renders a native-value
  transfer. An ERC-20 transfer's recipient and amount live inside `data`, where
  this module would show neither — that is precisely the blind-signing surface
  ERC-7730 exists to address.
- **As the durable record.** The issued, consumed, and unresolved sets live in
  process memory. A restart forgets which payloads were already submitted, which
  is the one piece of state that must survive a crash.
- **As a substitute for out-of-band verification.** CCSS v9 asks that fund
  destinations and amounts be verified over an Approved Communication Channel
  *before* key material is used. A display rendered by the vault defeats a
  compromised coordinator; it does not defeat an attacker who controls the
  address book the approver checks against.
- **Multi-party approval.** One vault, one approver. For M-of-N quorum, distinct
  roles, and timelocks see `multi-signature-approval-for-large-transfers`.

## Prerequisites

- Python 3.9+, standard library only.
- An **approval callback** wired into `OfflineAirGappedSigner`. Without one the
  vault refuses to sign anything — a vault with no approver is a blind-signing
  oracle, so the default is denial, not convenience.
- The **EIP-155 chain id** the vault is authorised for (`expected_chain_id`) and
  the chain the coordinator issues for (`chain_id`). Ethereum mainnet is `1`.
- A **broadcast adapter**: a callable taking the verified `UnsignedPayload` and
  returning a transaction reference string. Without one the coordinator reports
  `REJECTED` rather than pretending a submission occurred.
- Optional vault policy: `max_amount`, `allowed_destinations`,
  `enforce_monotonic_nonce`. These are your firm's numbers; no standard
  prescribes them.
- Physically isolated signing hardware with no Wi-Fi, Bluetooth, or cellular
  modem, and chain-of-custody controls over the QR/SD media.

## Workflow

1. **Issue a chain-bound intent.** `create_unsigned_transfer` validates the
   address and amount *before* consuming a nonce, so a rejected input does not
   burn one — a gap in a chain nonce sequence stalls every later transaction from
   that account. The intent carries the EIP-155 `chain_id`, because "ETH" alone
   does not distinguish mainnet from any other EVM chain sharing its address
   format.
2. **Export canonically.** `to_qr_code_data()` is sorted-key, separator-tight
   JSON, and `payload_hash()` is SHA-256 over exactly those bytes. Move it on QR
   or inspected SD media only. USB, Bluetooth, Wi-Fi, and cellular bridges defeat
   the air gap.
3. **Decode strictly offline.** The vault rejects malformed JSON, a missing or
   unexpected field, a v1 payload with no chain id, an amount over 18 decimals or
   outside the uint256 wei range, and a nonce that is a `bool`. Unknown fields are
   rejected rather than ignored, so media cannot carry data past the display.
4. **Apply vault policy before asking a human.** Wrong chain, off-allowlist
   destination, or over-ceiling amount are denied without ever prompting — an
   approver trained to click through machine-refusable cases is a weakened
   control.
5. **Refuse to re-sign.** The vault keeps its own signed-hash ledger and, by
   default, requires a strictly increasing nonce. The coordinator is the assumed
   adversary; its replay protection is worthless if it is the thing that is
   compromised.
6. **Clear-sign.** `clear_signing_display()` renders destination, amount, chain
   id, nonce, and payload hash **from the payload about to be signed**, and the
   approver must return exactly `True`. A truthy stub, a `None`, or a callback
   that raises is a denial.
7. **Return and verify the envelope.** The envelope crosses back as text via
   `to_transport_data()` / `from_transport_data()`, with its fields type- and
   shape-validated at construction — hostile media must fail closed, not raise a
   `TypeError` out of a comparison. The coordinator recomputes the hash, confirms
   it issued that intent, that the intent is not invalidated, that the chain
   matches, that the signer key id is known, that the payload is unconsumed, and
   only then checks the signature with `hmac.compare_digest`.
8. **Submit once, and treat ambiguity as ambiguity.** The payload is marked
   consumed *before* the adapter is called, so a crash mid-dispatch cannot be
   retried into a second submission. Any adapter failure returns `UNRESOLVED`,
   never `REJECTED`: the node may have accepted the transaction before the
   response was lost. Settle it with `resolve_unresolved()` against chain
   evidence, then issue a *new* intent with a new nonce — never resubmit the old
   envelope.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Blind signing.** Signing a hash without rendering the destination and amount
  from the bytes being signed lets a compromised coordinator redirect funds. In
  the February 2025 Bybit theft the signers' keys were never stolen; the
  interface they read described a transfer that was not the one they authorised,
  and ~$1.46bn left a cold wallet with a valid quorum on it.
- **Binding to a network label instead of a chain id.** "ETH" is a string. An
  intent approved for one EVM chain is meaningful on every other chain that
  shares the address format unless the chain id is inside the signed data —
  which is the whole point of EIP-155.
- **Retrying a broadcast because the response timed out.** A lost RPC response is
  *unknown*, not *failed*. Resubmitting is how one timeout becomes one double
  spend. Mark the payload consumed before dispatch, and reconcile against the
  chain rather than against your own optimism.
- **A boolean broadcast result.** Two states cannot express three. `BroadcastResult`
  deliberately raises on `bool()` so `if result:` cannot silently file
  `UNRESOLVED` under failure at every call site.
- **Trusting coordinator-side replay protection.** If the coordinator is the
  compromised component, its consumed-payload set is whatever the attacker says
  it is. The ledger that matters is the vault's.
- **Ignoring unknown payload fields.** Accepting a superset of the schema lets
  media smuggle fields past the approver's display and into whatever consumes the
  payload downstream.
- **Incrementing the nonce before validating the intent.** Every rejected address
  then burns a nonce, and the resulting gap stalls the account's queue.
- **Treating an approval callback's truthy return as approval.** A stub, a
  `MagicMock`, or a partially-initialised UI object is truthy. Require `is True`.
- **Reading the reference signature primitive as custody cryptography.** It is a
  symmetric HMAC. The online coordinator holds a key that can forge any
  signature. It is a test seam and nothing else.

## Verification

- Run the full round trip with an approver that records its argument, and confirm
  the display contains the destination, the amount, `Chain ID: 1`, the nonce, and
  a payload hash equal to `signed.original_payload_hash`.
- Construct `OfflineAirGappedSigner("k")` with no `approval_callback` and confirm
  `sign_qr_payload` returns `None` for a perfectly well-formed payload.
- Pass approvers returning `False`, `"yes"`, `object()`, `None`, and one that
  raises; confirm all five refuse to sign.
- Submit a payload with `chain_id=137` to a vault configured for `1` and confirm
  it is denied *without* the approval callback being invoked.
- Sign the same intent twice against one vault and confirm the second returns
  `None`; sign nonce 9 then nonce 4 and confirm the older one is refused unless
  `enforce_monotonic_nonce=False`.
- Feed a v1 payload (no `chain_id`) and confirm `AirGapSigningError`.
- Point a broadcast adapter at a `TimeoutError` and confirm the result is
  `UNRESOLVED`, the hash appears in `unresolved_payload_hashes`, a second
  `broadcast_to_network` returns `REJECTED` with `"payload already submitted"`,
  and the adapter was called exactly once.
- Confirm an adapter returning `""`, `None`, or a non-string is `UNRESOLVED`, not
  `ACCEPTED`.
- Confirm `bool(BroadcastResult(...))` raises `TypeError`.
- Construct envelopes with a non-string signature, a 3-character signature, an
  uppercase hash, and a blank signer id, and confirm each raises
  `AirGapSigningError` at construction rather than failing later.
- Invalidate an issued intent and confirm its envelope is rejected with
  `"intent was invalidated"` and never reaches the adapter.
- Submit `"MALICIOUS"` as a destination and confirm the next valid intent still
  receives the next consecutive nonce.
- Submit amounts of `0`, `-1`, `NaN`, `Infinity`, `1.0000000000000000001`,
  `str(2**256)`, and `1E+1000` and confirm each raises.
- Run `python -m unittest discover -s skills/air-gapped-signing-workflow-for-cold-storage/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `multi-signature-approval-for-large-transfers`
- `hardware-security-module-hsm-for-signing-keys`
- `crypto-wallet-key-custody-security`
- `test-transaction-verification-before-large-transfers`
- `exchange-withdrawal-whitelist-enforcement`
- `segregation-of-duties-for-custody-operations`
- `recovery-plan-for-lost-or-compromised-keys`
- `post-incident-forensics-for-suspected-key-compromise`
