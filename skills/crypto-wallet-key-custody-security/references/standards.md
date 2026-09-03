# Standards & Broker Coverage — crypto-wallet-key-custody-security

Primary sources (all consulted 2026-08-23):

- **Binance**, "Get API Key Permission" (Wallet REST API): https://developers.binance.com/docs/wallet/account/api-key-permission
- **Binance.US Support**, "API keys: Best practices & safety tips": https://support.binance.us/en/articles/9842812-binance-us-api-keys-best-practices-safety-tips
- **Coinbase Developer Platform**, "Get API Key Permissions" (Advanced Trade REST API): https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/data-api/get-api-key-permissions
- **Kraken**, "API key permissions": https://docs.kraken.com/exchange/guides/rest/api-keys
- **ERC-55**, "Mixed-case checksum address encoding": https://eips.ethereum.org/EIPS/eip-55
- **BIP-173**, "Base32 address format for native v0-16 witness outputs" (bech32): https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki
- **CryptoCurrency Security Standard (CCSS™)**, CryptoCurrency Certification Consortium (C4): https://cryptoconsortium.org/cryptocurrency-security-standard-documentation/overview/
- **NIST SP 800-57 Part 1 Rev. 5**, "Recommendation for Key Management: Part 1 – General", May 2020: https://csrc.nist.gov/pubs/sp/800/57/pt1/r5/final

## Exchange API Key Permission Vocabulary

No two exchanges name the funds-moving permission the same way. An audit written against one exchange's literal will silently pass another's.

| Exchange | Funds-moving permission fields | Other fields | Source |
|---|---|---|---|
| Binance | `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer` | `ipRestrict`, `enableReading`, `enableSpotAndMarginTrading`, `enableMargin`, `enableFutures`, `enableVanillaOptions`, `enablePortfolioMarginTrading`, `enableFixApiTrade`, `enableFixReadOnly`, `createTime` | Binance Get API Key Permission |
| Coinbase (Advanced Trade) | `can_transfer` — "Indicates whether the API key has deposit/withdrawal permissions" | `can_view`, `can_trade`, `portfolio_uuid`, `portfolio_type` | Coinbase CDP Get API Key Permissions |
| Kraken | "Withdraw Funds" (enables `Withdraw`, `WithdrawInfo`, `WithdrawCancel`, `WithdrawStatus`) | "Query Funds", "Query Open Orders & Trades", "Query Closed Orders & Trades", "Create & Modify Orders", "Cancel/Close Orders", "Query Ledger Entries", "Export Data", "Access WebSocket API", "Deposit Funds", "Earn" | Kraken API key permissions |

Note that Binance's `enableInternalTransfer` and `permitsUniversalTransfer` move funds without the substring "withdraw" appearing at all. "Deposit Funds" moves funds *in* and is not treated as funds-moving by this module.

## Address Comparison Semantics

| Format | Case handling | Consequence for whitelist comparison | Source |
|---|---|---|---|
| EVM / Ethereum (`0x` + 40 hex) | Mixed case is a **checksum only**, "backwards compatible with many hex parsers that accept mixed case". Net probability a mistyped address passes the check: 0.0247% | Compare case-INSENSITIVELY. A checksummed address and its lowercase form are the same address; comparing verbatim yields false "unapproved" alerts | ERC-55 |
| Bech32 (`bc1…`, `tb1…`) | "Encoders MUST always output an all lowercase Bech32 string"; "Decoders MUST NOT accept strings where some characters are uppercase and some are lowercase". Uppercase permitted for QR codes | Fold to lowercase; reject mixed case as invalid | BIP-173 |
| Base58Check (legacy BTC) | Base58 is a **case-sensitive alphabet** | Compare EXACTLY. Case-folding lets distinct addresses collide — a fail-open in the whitelist | BIP-173 (contrasts base58 mixed case with bech32) |

## Custody & Key Management Standards

| Topic | Standard | Source |
|---|---|---|
| Key protection by key type | NIST SP 800-57 Part 1 Rev. 5 specifies "the protection that each type of key and other cryptographic information requires" | NIST SP 800-57pt1r5 (May 2020) |
| Multi-signature controls | CCSS Level II incorporates "decentralized security technologies such as multiple signatures"; Level III requires "multiple actors required for all critical actions" and advanced authentication | C4 CCSS overview |
| Key/seed storage | CCSS Level 1 requires keys/seeds stored with strong encryption when not in use, a backup that exists, is protected against environmental risk, and is access-controlled | C4 CCSS |
| Withdrawal permission scoping | "Withdrawal permissions should only be enabled on keys that genuinely need to move funds, and those keys should have strict IP whitelisting. A compromised key with withdrawal permissions is a direct financial risk." | Kraken API key permissions |
| IP restriction and key expiry | Binance.US "will also reset API key permissions to read-only for inactive keys that are 1) unused for 90 days and 2) not secured by IP whitelisting" | Binance.US API key best practices |
| Hot/cold split ratio | **No standard exists.** The `max_hot_ratio = 0.15` default in this module is an engineering policy default, not a regulatory or industry threshold — calibrate to your own loss tolerance | Engineering default |
| Multi-sig threshold amount | **No standard exists.** `multisig_threshold` is disabled by default and must be set from your mandate | Engineering default |

Not verified: several secondary sources claim Binance requires IP whitelisting before withdrawals can be enabled on an API key. This module does **not** assert that — it is not stated in Binance's own developer or support documentation consulted above. Treat IP restriction as a strong recommendation plus the documented Binance.US 90-day read-only downgrade, not a universal precondition.

## Storage Backend Coverage

| Storage / Custody Solution | `StorageBackend` member | Relevance |
|---|---|---|
| AWS KMS | `AWS_KMS` | Encrypted envelope management for API secrets and private keys |
| GCP Cloud KMS | `GCP_KMS` | As above |
| Azure Key Vault | `AZURE_KEY_VAULT` | As above |
| HashiCorp Vault | `HASHICORP_VAULT` | Secret lease management, dynamic credentials, key access auditing |
| Hardware HSM | `HARDWARE_HSM` | Key material never leaves the device |
| Institutional custody (Fireblocks, BitGo, Coinbase Custody) | *not modelled* | Multi-signature policy engine, withdrawal whitelisting, quorums — audit via the custodian's own controls, not this module |

`ENV_VARIABLE` and `PLAINTEXT_FILE` are enumerated as known-insecure. Anything not in the secure allowlist — including unrecognized or misspelled values — is reported as insecure.

## Regulatory & Operational Notes

Intersects with institutional crypto custody requirements, SOC 2 Type II access controls, and exchange API key security policies. Jurisdiction-specific custody rules (e.g. qualified-custodian requirements) are out of scope here — see `regulatory-custody-requirements-by-jurisdiction`.
