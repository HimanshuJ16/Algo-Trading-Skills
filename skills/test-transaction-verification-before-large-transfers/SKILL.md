---
name: test-transaction-verification-before-large-transfers
description: >-
  Pre-signing policy gate for high-value crypto transfers. Requires a dust test
  transaction bound to the request's recipient address, chain and amount, an
  out-of-band counterparty receipt attestation, live confirmation depth, and a
  time-decay window latched at first confirmation — because confirmation depth
  alone confirms a wrong address just as reliably as a right one.
domain: Crypto Custody Security
subdomain: Treasury Transfer Verification & Destination Address Controls
tags: ["crypto-custody", "test-transaction", "dust-verification", "treasury", "address-whitelisting", "destination-tag", "confirmation-depth", "spend-verification"]
brokers_frameworks: ["Fireblocks", "BitGo", "Coinbase Custody", "Safe{Wallet} Smart Account", "CCSS v9", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a bot, treasury job, or ops runbook is about to move a
material amount of crypto to an external destination — treasury rebalancing, cold
storage movements, exchange deposits, OTC settlement, or multi-signature
disbursements — and you want a gate that refuses to release the primary payload
until a dust test transfer has actually demonstrated the destination is correct.

`TransferVerificationEngine` is a **local, in-process gate that runs before your
signing code**. It performs no network I/O. Every on-chain fact it reasons about —
confirmation depth, the recipient the dust actually landed at, the chain it landed
on — is supplied by the caller, who is responsible for having read it from a
trusted source. It produces an auditable record of why a transfer was or was not
authorised.

## When NOT to Use

- **As the enforcer.** Fireblocks, BitGo, Coinbase Custody, or your Safe policy
  module remain authoritative. If the custody platform refuses the transfer, that
  refusal stands regardless of what this engine returned. Configure the
  platform's own whitelist and approval quorum first; this gate is the layer that
  fails *before* a signed payload leaves your infrastructure.
- **As proof that an address is correct.** A test transfer is a *detection*
  control, not a guarantee. Fireblocks documents malware that lets the initial
  test transfer succeed and *then* swaps the deposit address before the main
  transfer, and notes that the messaging channels used to confirm receipt are
  themselves subject to spoofing and man-in-the-middle attacks. This skill
  reduces destination risk; it does not eliminate it.
- **With `require_counterparty_receipt=False`, for anything you care about.**
  That setting downgrades the gate to confirmation depth alone, which does not
  detect a wrong destination address at all (see the first pitfall below). It
  exists for destinations you control on both ends and where an out-of-band
  attestation is meaningless, not as a convenience.
- **With `allow_bypass_for_whitelisted=True` as a default posture.** That flag
  skips the test transfer entirely for whitelisted recipients at any notional. A
  previously used address is not evidence of a safe address.
- **As a substitute for signer quorum.** This gate verifies a *destination*. It
  does not collect or verify human approvals — see
  `multi-signature-approval-for-large-transfers`.

## Prerequisites

- Python 3.9+. Standard library only; no third-party dependencies.
- A trusted way to read back, for a broadcast transaction hash, the recipient
  address, the chain, the transferred amount, and the current confirmation depth
  — a node RPC, a block explorer API, or your custody provider's transaction
  endpoint. **This module does not query any of them.** It consumes what you pass
  it, so a caller that echoes back the address it *intended* to send to, instead
  of the one the chain actually recorded, defeats the entire control.
- An approved recipient whitelist, sourced from your HSM/MPC address book rather
  than from application config.
- A defined Approved Communication Channel for counterparty receipt confirmation
  (CCSS v9 `1.05.8.1`) — a callback to a pre-registered number, a signed message,
  or an authenticated ticket, not an inbound chat message.

## Workflow

1. **Configure policy.** Instantiate `VerificationConfig` with
   `large_transfer_threshold_usd`, `test_expiry_window_minutes`, and
   `require_counterparty_receipt` (leave it `True`). The threshold comparison is
   `>=`, so a transfer exactly at the threshold is treated as large.
2. **Register assets.** One `AssetConfig` per asset, giving `chain`,
   `min_confirmations`, `test_amount`, and `requires_destination_tag`. Set
   `min_confirmations` from **your custodian's published requirement**, not from a
   remembered default — venues differ materially for the same asset, and on
   chains with deterministic finality the number means something different
   entirely. See `references/standards.md`.
3. **Initiate.** Submit a `TransferRequest` to `initiate_transfer_request()`. It
   validates the notional (rejecting NaN/infinite/negative outright rather than
   comparing them), checks whitelist membership, and enforces destination tags.
   Below threshold returns `NOT_REQUIRED`; at or above it returns `TEST_PENDING`.
4. **Broadcast the dust transfer**, then **read the transaction back from the
   chain** and pass what you read to `record_test_transaction()` as
   `observed_recipient`, `observed_chain`, and `observed_amount`. If any of the
   three disagrees with the request, the engine raises
   `TestTransactionMismatchError` — do not catch it and retry. A mismatch means
   the dust went somewhere other than where the primary transfer is going, so
   the test has verified nothing and the discrepancy needs a human.
5. **Track depth.** Poll the chain and call `update_test_confirmations()`. The
   expiry clock is latched at the *first* crossing of `min_confirmations` and is
   never refreshed by later polls. If depth *drops* below the requirement — what
   a re-org looks like from the caller's side — the engine reverts the test to
   `TEST_PENDING` and clears the latch, so the window restarts from the
   re-confirmation.
6. **Obtain and record the counterparty receipt.** Contact the recipient over the
   Approved Communication Channel, have them confirm the dust arrived in *their*
   account, and record it with `acknowledge_test_receipt(attested_by, channel)`.
   This is the step that makes the test a control; step 5 on its own is not one.
7. **Authorise.** Call `verify_and_authorize_large_transfer()`. It re-checks the
   test-to-request binding, whitelist membership *at this moment* (revocations
   land immediately, including for in-flight requests), live confirmation depth,
   the receipt, and the expiry window. Authorisation is **single-use** — a second
   call raises, so one dust test cannot authorise a series of transfers.

## Common Pitfalls

- **Treating confirmation depth as destination verification.** This is the
  central mistake. A dust transfer to a typo'd or clipboard-poisoned address
  reaches 12 confirmations exactly as reliably as one to the correct address.
  Depth proves the network accepted a transfer to whatever address was in it and
  nothing more. Only the recipient confirming arrival out of band — CCSS v9
  `1.05.8.1`, "Verification of fund destinations and amounts is performed via
  Approved Communication Channels prior to the use of key material" — actually
  tests the address.
- **Recording the intended address instead of the observed one.** Passing the
  same variable you built the transfer from into `observed_recipient` turns the
  binding check into a tautology. Read it back from the chain.
- **Letting a monitoring loop refresh the expiry clock.** If the confirmation
  poller restarts the time-decay window on every update, the window never
  expires and a test transfer confirmed hours ago still authorises a transfer.
  Latch it once, at first confirmation.
- **Case-folding addresses to normalise them.** ERC-55 capitalisation is a
  checksum, so EVM addresses are safe to lowercase — but Base58Check (Bitcoin
  legacy, XRP), Solana base58, and TON base64 are case-**sensitive**. Lowercasing
  those collapses distinct addresses onto one whitelist key, so the whitelist
  starts approving addresses nobody whitelisted.
- **Comparing an unvalidated USD notional against the threshold.**
  `float('nan') >= 50_000` is `False` under IEEE-754, so a price feed that
  returns no quote silently classifies a large transfer as small and authorises
  it directly. Validate before comparing.
- **Omitted destination memos/tags.** XRP, XLM, TON, and EOS use a tag or memo to
  route deposits into the right customer account at a shared custodian address.
  An XRPL account can set the `RequireDest` flag to reject untagged payments, but
  it is opt-in — without it the payment succeeds on-chain and simply arrives
  uncredited. Note that **BNB is no longer in this category**: the memo-bearing
  BNB Beacon Chain (BEP-2) halted on 2024-11-19, and BEP-20 on BNB Smart Chain is
  EVM and uses no memo.
- **Re-verifying nothing at the authorisation gate.** Whitelists get revoked and
  chains re-org between initiation and release. Checks made minutes ago are not
  evidence about now.
- **Sending to a whitelisted address on the wrong network.** An address approved
  for Ethereum and used on Arbitrum is a permanent loss, and the whitelist alone
  will not catch it — the chain must be part of the binding.

## Verification

```bash
python -m unittest discover -s skills/test-transaction-verification-before-large-transfers/scripts
```

The suite covers threshold boundaries (exactly at, just below), NaN/infinite/
negative notional rejection, case-sensitivity of Base58 versus EVM versus bech32
whitelist keys, test-transaction binding to recipient/chain/amount, the
counterparty-receipt requirement, expiry-window latching under sustained polling,
re-org depth regression and re-confirmation, single-use authorisation, and
whitelist revocation of an in-flight request.

## Related Skills

- `multi-signature-approval-for-large-transfers`
- `exchange-withdrawal-whitelist-enforcement`
- `air-gapped-signing-workflow-for-cold-storage`
- `crypto-wallet-key-custody-security`
- `withdrawal-velocity-limits-and-anomaly-detection`
