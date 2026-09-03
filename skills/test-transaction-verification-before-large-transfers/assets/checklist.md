# Large Crypto Transfer — Test Transaction Sign-Off Checklist

One pass per transfer. Anything unticked blocks release.

## 1. Pre-flight policy

- [ ] **Notional is a real, finite, non-negative number.** A NaN from a dead
      price feed compares `False` against any threshold and would classify a
      large transfer as small.
- [ ] **Recipient is on the HSM/MPC whitelist**, sourced from the address book
      rather than application config.
- [ ] **Address matched byte-exact where the encoding demands it.** EVM hex and
      bech32 fold case safely; Base58Check (BTC legacy, XRP), Solana base58 and
      TON base64 do not.
- [ ] **Checksum validated at address-book entry** (EIP-55 / Base58Check /
      bech32). The engine matches against the whitelist; it does not verify
      checksums.
- [ ] **Destination tag/memo supplied** for XRP, XLM, TON, or EOS. Not required
      for BNB — BEP-2 was sunset 2024-11-19 and BEP-20 carries no memo.
- [ ] **`min_confirmations` sourced from this custodian's published requirement**,
      not a remembered default. Venues differ (Kraken 4 BTC / 20 ETH; Coinbase
      2 / 14).
- [ ] **Threshold calibrated** (`large_transfer_threshold_usd`). Comparison is
      `>=`, so a transfer exactly at the threshold is gated.
- [ ] **`allow_bypass_for_whitelisted` is `False`** unless a named risk owner has
      signed off on the exception for this specific run.
- [ ] **`require_counterparty_receipt` is `True`.** If disabled, record who
      approved reducing this to a depth-only check and why.

## 2. Dust test transfer

- [ ] **Amount clears the relay floor** for the output type (BTC: 546 sat P2PKH /
      294 sat P2WPKH / 330 sat P2WSH & P2TR at the default `dustRelayFee`) and any
      chain-specific account reserve.
- [ ] **Amount is small relative to the gated transfer.** A fixed native-token
      amount is not a fixed USD amount — re-derive if the price has moved.
- [ ] **Transaction read back from the chain**, not from the request object, and
      `observed_recipient` / `observed_chain` / `observed_amount` passed to
      `record_test_transaction()` from that read-back.
- [ ] **No `TestTransactionMismatchError` was caught and retried.** If one fired,
      it is escalated to a human and this transfer is stopped.

## 3. Confirmation and receipt

- [ ] **Required depth reached** and still holding at the moment of authorisation.
- [ ] **No unexplained depth regression** since first confirmation. If depth
      dropped, treat the earlier confirmation as void and restart the window.
- [ ] **Counterparty confirmed arrival over an Approved Communication Channel**
      (CCSS v9 `1.05.8.1`) — an outbound callback to a pre-registered contact, a
      signed message, or an authenticated ticket. An inbound chat message is not
      an approved channel.
- [ ] **Attestation recorded** via `acknowledge_test_receipt(attested_by,
      channel)`, naming a person and the channel used.

## 4. Release

- [ ] **Authorisation obtained within the expiry window**, measured from first
      confirmation.
- [ ] **Whitelist re-checked at the authorisation gate** — the engine does this;
      confirm no revocation fired.
- [ ] **Authorisation consumed exactly once.** No retry loop re-requested it.
- [ ] **Audit trail persisted**: request ID, test tx hash, observed recipient and
      chain, confirmation depth, attesting party and channel, approval timestamp.
- [ ] **Approved payload submitted to the custody vault** (Fireblocks / BitGo /
      Safe) for its own policy evaluation and signing quorum. The vault, not this
      gate, is the enforcer.

## Residual risk acknowledged

- [ ] Team understands a passing test transfer is **not** proof the address is
      correct. Malware that lets the test succeed and then swaps the deposit
      address is documented, and the confirmation channel itself is subject to
      spoofing and man-in-the-middle attacks.
