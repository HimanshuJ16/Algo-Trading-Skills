# Standards for Air-Gapped Signing in Cold Storage

## Status of these requirements

The physical-isolation, media-handling, and separation-of-duties rules below are
**operational policy**, not regulation. No securities or virtual-asset regulator
surveyed here mandates QR-versus-SD transfer, a nonce ceiling, or an amount
threshold. What *is* externally grounded is the shape of three controls — chain
binding in the signed data, verification of destination and amount before key
use, and human-readable review instead of blind signing — and those sources are
cited below. Every numeric policy value (`max_amount`, the allowlist, the
monotonic-nonce rule) is your firm's to set and defend.

## Engineering standards enforced by the module

| Standard | Rule | Enforced by |
|---|---|---|
| Chain binding | The intent carries an EIP-155 `chain_id`; a network label alone is not a chain. A vault signs only its configured chain, and the coordinator rejects an envelope for another. | `UnsignedPayload.__post_init__` / `_passes_policy` / `_verify_envelope` |
| No blind signing | The approval display is derived from the exact payload about to be signed, and signing requires the approver to return `True` by identity. | `clear_signing_display` / `_obtain_human_approval` |
| Deny by default | A vault with no `approval_callback` signs nothing. A callback that raises, or returns a truthy non-`True`, is a denial. | `_obtain_human_approval` |
| Policy before prompt | Wrong chain, off-allowlist destination, or over-ceiling amount are refused without prompting a human. | `_passes_policy` |
| Vault-side replay ledger | The vault refuses to re-sign a payload hash it has already signed, and by default requires a strictly increasing nonce — independently of the coordinator, which is the assumed adversary. | `_passes_replay_checks` |
| Canonical payload | Sorted-key, separator-tight, ASCII-safe JSON; SHA-256 over exactly those bytes. An exact field-set match rejects both missing and unknown fields. | `to_qr_code_data` / `_decode_json_object` |
| Exact amount bounds | At most 18 decimals, positive, finite, and within the uint256 wei range, computed from the digit tuple rather than by rounded `Decimal` arithmetic. | `_canonical_amount` |
| Fail closed on hostile media | Envelope fields are type- and shape-checked at construction, so a non-string signature raises `AirGapSigningError` rather than a `TypeError` from a comparison. | `SignedPayload.__post_init__` |
| Nonce integrity | The coordinator commits a nonce only after the intent validates, so a rejected input leaves no gap. | `create_unsigned_transfer` |
| Submit once | The payload is recorded as consumed *before* the adapter is called; a replay never reaches the RPC layer. | `broadcast_to_network` |
| Ambiguity is not failure | Any adapter exception or missing reference yields `UNRESOLVED`, and `BroadcastResult` raises on `bool()` so the state cannot be silently coerced. | `BroadcastStatus` / `BroadcastResult.__bool__` |
| Constant-time comparison | Signature equality uses `hmac.compare_digest`. | `_verify_envelope` |

## External touchpoints (verified 2026-09)

| Source | Identifier | What it actually says | How this skill relates |
|---|---|---|---|
| Ethereum Improvement Proposals | EIP-155, *Simple replay attack protection* | Signing hashes nine RLP elements `(nonce, gasprice, startgas, to, value, data, chainid, 0, 0)` rather than six, and `v` becomes `{0,1} + CHAIN_ID * 2 + 35`, so a signature is valid only on the chain it names. The EIP lists mainnet as `CHAIN_ID` 1. | Why `chain_id` is a mandatory payload field and why v1 payloads are rejected outright rather than defaulted. The module does not build a real signed transaction; it binds the *intent* to a chain for the same reason. |
| Ethereum ERCs | ERC-7730, *Structured Data Clear Signing Format* (**Draft**) | "Properly validating a transaction on a hardware wallet's screen (also known as Clear Signing) is a key element of good security practices… most data to sign, even enriched with the data structure description (like ABIs or EIP-712 types) are not self-sufficient in terms of correctly displaying them to users for review." A voluntary JSON metadata format, not a mandate. | Names the problem `clear_signing_display` addresses. Cite it as the standard for *contract-call* display; this module renders a native transfer and would show nothing useful for an ERC-20 `data` field — see "When NOT to Use". |
| CryptoCurrency Certification Consortium | CCSS v9, `1.05.8.1` (Level II) | "Verification of fund destinations and amounts is performed via Approved Communication Channels prior to the use of key material." | The out-of-band destination check in the checklist. Note this is *in addition to* the vault display: it defends the address book, which an on-device display cannot. |
| CryptoCurrency Certification Consortium | CCSS v9, `1.05.2.1` (Level I) | "Key material is only used within the CCSS Trusted Environment." | The offline-vault boundary: the private key never leaves `OfflineAirGappedSigner`, and the class models no network surface. |
| CryptoCurrency Certification Consortium | CCSS v9, `1.01`, `1.03` | Key material is generated by the actor who will use it; the generating system is taken **offline** before generation (CCSS does not require it be *permanently* air-gapped). Storage requires strong encryption at rest and a backup, with geographic separation at Level II. | Key generation and backup are out of scope here — see `shamir-secret-sharing-for-key-backup` and `cold-storage-geographic-distribution-strategy`. Do not claim CCSS mandates a permanent air gap; it does not. |

## Incident evidence

| Incident | Confirmed facts | Consequence for this skill |
|---|---|---|
| Bybit, 21 February 2025 | ~$1.46bn left an Ethereum cold wallet — the largest crypto theft recorded. Bybit's account: hackers "exploited the UI of the Safe multisig cold wallet through a sophisticated phishing attack, masking the specific transaction, which resulted in the change in smart contract logic". The signing keys themselves were **not** compromised; authorised signers approved what they were shown. | The display, not the key, was the attacked surface. Hence `clear_signing_display()` is derived from the payload that will be signed, the approval callback receives that exact text, and the payload hash is shown so it can be compared out of band. It is also why the vault — not the online coordinator — owns the policy and replay checks. |

## What this module deliberately does not claim

- It is not cryptography. The HMAC primitive is symmetric, so the online
  coordinator's `verification_key` can forge any signature. Real custody signs
  with secp256k1 in a hardware wallet or HSM and keeps only a public key online.
- It does not construct, RLP-encode, or broadcast a real Ethereum transaction,
  and it models no gas parameters or `data` field.
- It holds no durable state, so it cannot by itself prevent a double submission
  across a restart or across two coordinator processes.
- It is single-signer. It makes no quorum, role-separation, or timelock claim.

## Sources

- EIP-155, Simple replay attack protection — https://eips.ethereum.org/EIPS/eip-155
- ERC-7730, Structured Data Clear Signing Format — https://ercs.ethereum.org/ERCS/erc-7730
- ERC-7730 clear-signing metadata registry — https://github.com/ethereum/clear-signing-erc7730-registry
- CryptoCurrency Certification Consortium, CCSS v9 requirements matrix — https://cryptoconsortium.org/ccss-table-v9/
- Bybit, "Bybit Security Incident: Timeline of Events and FAQs" — https://learn.bybit.com/en/this-week-in-bybit/bybit-security-incident-timeline
- Elliptic, "The largest theft in history — following the money trail from the Bybit Hack" — https://www.elliptic.co/blog/bybit-hack-largest-in-history
