---
name: exchange-withdrawal-whitelist-enforcement
description: >-
  Client-side pre-flight gate for automated exchange withdrawals that binds every
  request to an allowlisted (asset, network, address, memo) destination, enforces a
  tamper-resistant cool-off lock against a trusted clock, and blocks withdrawal-capable
  API keys that carry no IP restriction.
domain: Crypto Custody & Security
subdomain: Exchange Security & Address Whitelisting
tags: ["withdrawal-whitelist", "address-allowlist", "crypto-custody", "cooloff-lock", "api-key-drain", "destination-tag", "network-scoping"]
brokers_frameworks: ["Binance SAPI Wallet", "Coinbase Exchange Address Book", "Kraken Global Settings Lock", "OKX Allowlist", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a bot, treasury job, or ops script can move crypto **off** an exchange without a human in the loop. It is a pre-flight gate that runs inside your infrastructure: it decides whether a withdrawal request may be signed and submitted at all, and it leaves an auditable record of why. The checks it runs, in order, are API key withdrawal scope, API key IP restriction, allowlist membership scoped to `(asset, network, address)`, revocation, destination tag binding, and the cool-off lock.

It exists because whitelisting is the control that survives key compromise. An attacker holding a withdrawal-capable API key cannot reach an address that is not on the list, and the cool-off means an address they *add* is unusable long enough for the alert to land.

## When NOT to Use

- **As a replacement for the venue's own whitelist.** The exchange is the authoritative enforcer. A local allowlist alone protects nothing: an attacker with your key calls the venue directly and never runs this code. Turn the venue's whitelist on first; this gate is the second layer.
- **For anything other than a go/no-go on one request.** Amount ceilings, velocity limits, and anomaly scoring are not modelled — see `withdrawal-velocity-limits-and-anomaly-detection`.
- **For on-chain self-custody transfers.** This models an exchange's withdrawal surface, not a wallet's signing policy. See `hot-cold-wallet-split-for-trading-bots` and `multi-signature-approval-for-large-transfers`.
- **For "universal"/any-network allowlist entries.** Binance offers an address entry that applies to any crypto; this engine deliberately refuses to model it, because an entry that is not bound to a chain cannot catch a wrong-chain withdrawal.

## Prerequisites

- The venue's whitelist feature actually **enabled** on the account. On Binance and OKX it is opt-in and off by default.
- Allowlist records carrying `asset_symbol`, `network`, `destination_address`, `added_timestamp_seconds`, and `destination_tag` where the chain uses memos. Sync these from the venue rather than maintaining a parallel list that drifts.
- Per-network constraints from the venue — on Binance, the `addressRegex`, `memoRegex` and `withdrawTag` fields of `GET /sapi/v1/capital/config/getall`. Without a registered `NetworkWithdrawalPolicy` the engine cannot check address shape or mandatory memos, and every report says so in `warnings`.
- A **trusted clock** for `evaluation_timestamp_seconds`. Pass it explicitly for reproducible audits.
- API key facts: `is_withdrawal_enabled_on_key` and `is_key_ip_restricted`. Both default to `False` — deny by default.
- A serialisation point around the whole audit-then-submit sequence. The engine is **not** thread-safe, and an approval is a decision about a point in time: an operator can revoke an address between the approval and the submission.

## Workflow

1. **Gate the Key Before the Address**: Reject a request whose key is not withdrawal-scoped, then reject a withdrawal-capable key carrying no IP access restriction. Binance will not enable withdrawal permission on an unrestricted key at all, so that combination means either another venue or a stale config — treat it as a finding, not a warning. The report reports allowlist membership as `None`, not `False`, because that check never ran.
2. **Look Up the Destination by `(asset, network, canonical address)`**: The same address string is valid on Ethereum, BSC, Polygon and Arbitrum, and Binance scopes whitelist entries per coin *and* per network. Canonicalisation folds case only where the encoding is case-insensitive: EVM hex (ERC-55 capitalisation is a checksum, not part of the address) and all-uppercase bech32 (BIP-173 treats it as the same address). Base58Check — Bitcoin legacy, Tron, XRP — is returned byte-exact, because folding case there maps distinct address strings onto one allowlist key.
3. **Check Revocation Before Anything Else Passes**: A revoked entry is retained, not deleted, so an attempt against it returns `ADDRESS_REVOKED_REJECTION` — a far stronger operational signal than the generic "unknown address" a deletion would produce.
4. **Bind the Memo, Not Just the Address**: On XRP, XLM, EOS and ATOM the address identifies a shared venue wallet and the memo identifies the account inside it. Compare the request's tag against the tag bound to the allowlist entry, and reject a swapped, dropped, or unexpected tag. A missing memo on a `withdrawTag` network is not a soft warning: the funds arrive and are credited to nobody.
5. **Evaluate the Cool-off Against the Trusted Clock, Never the Request's Own Timestamp**: `request_timestamp_seconds` travels with a potentially attacker-controlled request. A lock you can open by writing a future number into the request is not a lock. The request timestamp is recorded and skew-checked only. The effective cool-off is `max(record value, engine floor)`, so a record arriving with `cooloff_duration_seconds=0` cannot disable the control. The unlock boundary is inclusive: `now >= anchor + cooloff` approves.
6. **Keep the Unlock Time Monotonic on Re-registration**: Registering an address that already exists takes the *later* anchor and the *longer* cool-off, and an address that was revoked re-anchors to the revocation time. Without this, back-dating an in-cool-off entry clears its lock in a single call. Suppressed regressions are logged as a tamper signal — alert on them.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating "24 hours" as a Standard**: There is no universal mandatory lock. Binance's Whitelist Withdrawal Limit is opt-in and offers **24, 48 or 72 hours**; Coinbase Exchange holds a new address book entry **48 hours**; OKX's new-address lock is an opt-in **24 hours**; Kraken's hold is triggered by a *password change* without Sign-in 2FA or a Master Key (12h on Kraken Pro, 24h on Kraken Classic). Hard-coding 24h either under-enforces your own policy or misreports the venue's. Set the record value from the venue and the floor from your firm.
- **Measuring the Cool-off From a Timestamp the Caller Supplies**: If the elapsed time is `request_timestamp - added_timestamp`, anything that can build a request can unlock any address by setting a future timestamp. Use a server clock the request cannot influence.
- **Omitting `network` on the Withdraw Call**: `network` is *optional* on Binance's `POST /sapi/v1/capital/withdraw/apply` and falls back to the coin's default network. Approve on `(asset, address)` alone and you will eventually send USDT over the default chain to an address that only exists on another one. Those funds are usually unrecoverable.
- **Whitelisting the Address but Not the Memo**: A request that keeps a whitelisted XRP address and swaps the destination tag passes every address-only check, lands at the right exchange, and credits somebody else's account.
- **Letting a Re-add Reset the Lock**: An allowlist sync that overwrites on write lets a hostile or buggy "refresh" of an in-cool-off address, carrying an older `added_timestamp`, clear the lock immediately. Anchor monotonically.
- **Defaulting the Permission Flags to `True`**: A security control whose fields default to "permitted" silently authorises every caller that forgets to set them. Deny by default and make the caller assert the permission.
- **Trusting the Local Allowlist Instead of the Venue's**: This gate constrains *your* code. It does nothing about a stolen key used directly against the venue's API. Enable the venue's whitelist, restrict the key by IP, and keep withdrawal scope off every key that does not need it — see `api-key-least-privilege-audit-tool`.
- **Letting NaN Through the Comparisons**: `float('nan') < 86_400.0` is `False`, so a NaN elapsed time or amount falls straight through a naive rejection branch to approval. Every numeric input is validated as finite and raises `WithdrawalWhitelistError` rather than being scored.

## Verification

- Register a cold-storage entry added 48h ago under a 24h cool-off and confirm `WITHDRAWAL_APPROVED`; request an unregistered address and confirm `UNAUTHORIZED_ADDRESS_REJECTION`; request an entry added 2h ago and confirm `COOLOFF_PERIOD_ACTIVE_REJECTION` with `remaining_cooloff_seconds == 79_200.0`.
- Submit a request carrying `request_timestamp_seconds` far in the future against an in-cool-off address and confirm it is still `COOLOFF_PERIOD_ACTIVE_REJECTION`, with a clock-skew entry in `warnings`.
- Re-register an in-cool-off address with an older `added_timestamp_seconds`, then with `cooloff_duration_seconds=0.0`, and confirm the lock is unchanged in both cases.
- Revoke an unlocked address, confirm `ADDRESS_REVOKED_REJECTION`, re-add it with its original timestamp, and confirm it serves a fresh full cool-off.
- Whitelist an EVM address for `USDT:ETH` and confirm the same address is rejected under `USDT:BSC`; confirm an ERC-55 checksummed request matches a lowercase entry, and that a lowercased Base58Check address does **not** match its mixed-case entry.
- Bind an XRP entry to tag `1234567` and confirm a swapped, dropped, or whitespace-only tag returns `DESTINATION_TAG_MISMATCH_REJECTION`.
- Submit `amount=float('nan')`, `amount=0.0`, and a blank destination address, and confirm each raises `WithdrawalWhitelistError` rather than producing a decision.
- Run `python -m unittest discover -s skills/exchange-withdrawal-whitelist-enforcement/scripts` and confirm a 100% pass rate.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `test-transaction-verification-before-large-transfers`
- `api-key-least-privilege-audit-tool`
- `multi-signature-approval-for-large-transfers`
- `exchange-proof-of-reserves-verification`
