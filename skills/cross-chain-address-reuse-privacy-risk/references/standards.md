# Standards for Cross-Chain Address Privacy Risk

| Metric | Engineering Standard |
|---|---|
| Zero Cross-Chain Address Reuse | A wallet address or public key MUST NOT appear on more than one chain; any reuse ($K > 1$ chains) is flagged by the auditor. A cluster confined to a single chain ($K = 1$) is no reuse and carries zero reuse weight. |
| Unrevealed Key Material | A public key that has not been revealed on-chain MUST be recorded as absent (`None`), never as a placeholder string. Placeholder values create linkage edges between unrelated wallets and propagate KYC contamination across them. Bitcoin P2PKH/P2SH keep the key hashed "until the first time satoshis sent to those addresses are spent". |
| Chain Label Normalisation | Chain identifiers are registry labels, not protocol constants; they MUST be normalised (strip + case-fold) before counting distinct chains, or label variants inflate the reuse metric. |
| KYC Isolation | Trading strategy execution wallets MUST NOT deposit directly to KYC-verified exchange addresses; one KYC linkage contaminates the entire linked cluster. |
| BIP-44 Path Separation | Wallets MUST use distinct BIP-44 paths $m/44'/coin\_type'/account'/change/address\_index$ per chain family and strategy. `coin_type` values follow the SLIP-44 registry: Bitcoin $0'$, Ethereum/EVM $60'$, Solana $501'$. Because EVM address derivation (last 20 bytes of Keccak-256 of the public key) takes no chain parameter, one key pair yields the identical `0x` address on every EVM network — EVM-to-EVM isolation therefore requires distinct `account'` indexes (or separate seeds), not merely `coin_type`. Ed25519 chains (Solana) derive per SLIP-0010, which supports hardened derivation only. |
| Registry Identifier Hygiene | Address comparison MUST be case-insensitive only for `0x`-prefixed hex (EIP-55 mixed case is a checksum layer); base58 identifiers (Bitcoin legacy, Solana) MUST be compared case-sensitively — both letter cases are distinct base58 characters. |

## Primary Sources

- BIP-44 — Multi-Account Hierarchy for Deterministic Wallets (path levels; hardened `coin_type`): https://github.com/bitcoin/bips/blob/master/bip-0044.mediawiki
- SLIP-44 — Registered coin types (Bitcoin `0'`, Ethereum `60'`, Solana `501'`, and per-network L2 registrations): https://github.com/satoshilabs/slips/blob/master/slip-0044.md
- SLIP-0010 — Universal HD wallets; Ed25519 hardened-only derivation: https://github.com/satoshilabs/slips/blob/master/slip-0010.md
- EIP-55 — Mixed-case checksum address encoding (case is display/checksum, not address data): https://eips.ethereum.org/EIPS/eip-55
- Ethereum account address derivation (last 20 bytes of Keccak-256 of the public key; no chain parameter): https://ethereum.org/en/developers/docs/accounts/
- Solana account structure (base58-encoded Ed25519 public keys): https://solana.com/docs/core/accounts/account-structure
- Bitcoin Developer Guide — "Unique (non-reused) P2PKH and P2SH addresses protect against the first type of attack by keeping ECDSA public keys hidden (hashed) until the first time satoshis sent to those addresses are spent": https://developer.bitcoin.org/devguide/transactions.html
- Chainalysis — address clustering and reuse as a clustering signal: https://www.chainalysis.com/glossary/address-clustering/
