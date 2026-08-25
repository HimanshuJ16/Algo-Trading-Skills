# Standards for Exchange Withdrawal Whitelist Enforcement

## Status of these requirements

Withdrawal address allowlisting is **exchange product policy, not regulation**. No
securities or market regulator mandates a 24-hour address cool-off. The "MUST"
statements below are therefore *engineering* standards for this skill's own gate,
and the venue table records what each exchange actually does. Do not present either
as a regulatory obligation.

## Engineering standards enforced by the engine

| Standard | Rule | Enforced by |
|---|---|---|
| Deny by default | API key permission flags default to `False`. A request that does not assert withdrawal scope is rejected. | `WithdrawalRequest.is_withdrawal_enabled_on_key` / `.is_key_ip_restricted` |
| IP-restricted keys | A withdrawal-capable key with no IP access restriction is rejected. | `require_ip_restricted_key` (default on) |
| Chain-bound allowlist | Entries are keyed by `(asset, network, canonical address)`. A "universal"/any-network entry is not modelled. | `_allowlist` key |
| Memo binding | The destination tag is part of the whitelisted destination, not an argument the caller may vary. | `_check_destination_tag` |
| Trusted clock | The cool-off is measured against `evaluation_timestamp_seconds`, never `request_timestamp_seconds`. | `audit_withdrawal_request` |
| Cool-off floor | Effective cool-off is `max(record value, minimum_cooloff_seconds)`; a record cannot shorten the lock below the firm floor. | `minimum_cooloff_seconds` |
| Monotonic unlock | Re-registration takes the later anchor and longer cool-off; re-adding a revoked entry re-anchors to the revocation time. | `register_whitelisted_address` |
| Fail loudly | Non-finite/non-positive amounts, blank addresses, and negative cool-offs raise rather than being scored. | `WithdrawalWhitelistError` |
| Honest audit record | Checks that did not run report `None`, not `False`; `checks_evaluated` names what executed. | `WithdrawalWhitelistAuditReport` |

## Venue behaviour (verified 2026-08)

| Venue | Whitelist | New-address lock | Notes |
|---|---|---|---|
| Binance | Opt-in ("Withdrawal Whitelist") | **24 / 48 / 72 h**, user-selected, via the separate "Whitelist Withdrawal Limit" | Suspension applies only to newly added addresses. Entries are scoped per coin **and** per network, or may be set as a universal address. |
| Coinbase Exchange | Address Book allowlisting | **48 h** hold on a newly added entry | The entry may be deleted during the hold; withdrawals to it are blocked until the hold ends. |
| OKX | Opt-in allowlist | **24 h** via the "New address withdrawal lock" advanced setting | Removed automatically after 24 h; the setting can be disabled with mobile + email verification. |
| Kraken | Whitelist plus Global Settings Lock (GSL) | Trigger differs: a **password change** without Sign-in 2FA or a Master Key holds withdrawals to new addresses — **12 h** (Kraken Pro) / **24 h** (Kraken Classic) | With GSL enabled a withdrawal address cannot be added at all; the unlock runs on an operator-chosen delay. A new-address approval email link expires after one hour. |

The spread — opt-in vs. always-on, 12h to 72h, triggered by address-add vs. by
password-change — is why the cool-off is a per-record value here and not a constant.

## API surface relied on

| Item | Detail | Source |
|---|---|---|
| `POST /sapi/v1/capital/withdraw/apply` | `network` is **optional**; when omitted the coin's default network is used. `addressTag` is the memo for coins such as XRP and XMR. Supplying `addressTag` on a network that does not support one returns error `-4106`. | Binance wallet API docs |
| `GET /sapi/v1/capital/config/getall` | Per-network fields include `addressRegex`, `memoRegex`, `withdrawTag` (`"true"` ⇒ memo required), `withdrawMin`/`withdrawMax`, `withdrawIntegerMultiple`, `sameAddress`. | Binance wallet API docs |
| API key withdrawal scope | Withdrawal permission cannot be enabled on a Binance API key that carries no IP access restriction. | Binance API management |

`NetworkWithdrawalPolicy` mirrors `addressRegex`, `memoRegex` and `withdrawTag`
deliberately, so the venue's own constraints can be loaded rather than re-derived.

## Address encoding rules the canonicaliser depends on

| Encoding | Case semantics | Handling |
|---|---|---|
| EVM hex (`0x` + 40 hex) | Case-insensitive. ERC-55 encodes a checksum in capitalisation; all-lowercase and all-uppercase forms remain valid and denote the same address. | Folded to lowercase. |
| Bech32 / bech32m (BIP-173, BIP-350) | Mixed case is invalid — "Decoders MUST NOT accept strings where some characters are uppercase and some are lowercase." Encoders MUST output lowercase; uppercase is used for QR codes. | Folded to lowercase only when the whole string is uppercase. |
| Base58Check (BTC legacy, Tron, XRP) and Solana base58 | Case-**sensitive**. | Never folded; matched byte-exact. |

Folding case on a case-sensitive encoding would collapse distinct address strings
onto a single allowlist key, which is why the canonicaliser is encoding-aware
rather than calling `.lower()` on everything.

## Sources

- Binance, "How to Enable Withdrawal Whitelist for My Binance Account?" — https://www.binance.com/en/support/faq/how-to-enable-withdrawal-whitelist-on-binance-1d08944f103b4fc78d3519913b600086
- Binance Wallet API, Withdraw — https://developers.binance.com/docs/wallet/capital/withdraw
- Binance Wallet API, All Coins' Information — https://developers.binance.com/docs/wallet/capital/all-coins-info
- Coinbase Help, "Restrict crypto withdrawal from Coinbase Exchange to external addresses" — https://help.coinbase.com/en/exchange/managing-my-account/how-does-whitelisting-in-the-address-book-work
- OKX Help, "How do I enable allowlist?" — https://www.okx.com/en-us/help/how-do-i-enable-allowlist-web
- Kraken Support, "Adding and confirming a new cryptocurrency withdrawal address" — https://support.kraken.com/articles/360000672863-adding-and-confirming-a-new-cryptocurrency-withdrawal-address
- Kraken Support, "What is the Global Settings Lock (GSL)?" — https://support.kraken.com/articles/201396877-what-is-the-global-settings-lock-gsl-
- BIP-173, Base32 address format for native v0-16 witness outputs — https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki
- ERC-55, Mixed-case checksum address encoding — https://eips.ethereum.org/EIPS/eip-55
