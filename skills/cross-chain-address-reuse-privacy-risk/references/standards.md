# Standards for Cross-Chain Address Privacy Risk

| Metric | Engineering Standard |
|---|---|
| Zero Cross-Chain Address Reuse | Proprietary trading bots MUST NOT reuse identical EVM addresses across $>2$ chains. |
| KYC Isolation | Trading strategy execution wallets MUST NOT deposit directly to KYC-verified exchange addresses. |
| BIP-44 Path Standards | Wallets MUST use unique BIP-44 `coin_type` and `account` index derivation paths per chain/strategy. |