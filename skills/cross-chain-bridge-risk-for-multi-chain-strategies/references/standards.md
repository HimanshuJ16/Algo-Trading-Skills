# Standards for Cross-Chain Bridge Risk Management

Primary sources (re-verified 2026-08-23; all URLs returned HTTP 200):

- **Arbitrum docs** — "a 6.4-day challenge period" on assertions, alongside "a seven-day challenge period to safeguard withdrawals" on the canonical bridge: https://docs.arbitrum.io/how-arbitrum-works/inside-arbitrum-nitro
- **Optimism docs** — Standard Bridge withdrawals "require a minimum 7-day wait"; distinct from L2 finality stages (~2 s soft, ~15-30 min hard): https://docs.optimism.io/op-stack/transactions/transaction-finality
- **Chainalysis (primary)** — "$2 billion in cryptocurrency has been stolen across 13 separate cross-chain bridge hacks"; "attacks on bridges account for 69% of total funds stolen in 2022 so far" (published 2022-08-02): https://www.chainalysis.com/blog/cross-chain-bridge-hacks-2022/
- **Chainalysis (primary)** — Ronin: "more than $600 million", Lazarus Group obtained "five of the nine private keys held by transaction validators": https://www.chainalysis.com/blog/axie-infinity-ronin-bridge-dprk-hack-seizure/
- **Chainalysis (primary)** — Wormhole: "roughly 120,000 Wormhole Ethereum (WeETH) worth over $320 million", minted "without putting up the necessary equivalent Ethereum collateral": https://www.chainalysis.com/blog/wormhole-hack-february-2022/
- **Chainalysis (primary)** — 2025 theft mix, showing bridges are no longer the leading vector: centralized-service compromises at 88% of Q1 2025 losses, personal-wallet compromises at 20% of value stolen, DeFi suppressed: https://www.chainalysis.com/blog/crypto-hacking-stolen-funds-2026/
- **Google Cloud Threat Intelligence** — Nomad bridge hack forensics: https://cloud.google.com/blog/topics/threat-intelligence/dissecting-nomad-bridge-hack
- **Blockworks** — Nomad $190M raid: https://blockworks.com/news/nomad-token-bridge-raided-for-190m-in-frenzied-free-for-all

> **Currency caveat.** The bridge-loss statistics above are 2022 figures and are cited as the historical justification for concentration caps, not as a claim about the current threat mix. As of Chainalysis's 2025 data, centralized-service and personal-wallet compromises dominate stolen value. Bridge exploits remain low-frequency/high-severity — which is what a cap is for.

| Metric | Engineering Standard | Source |
|---|---|---|
| Single Bridge NAV Cap | Default $\le 15\%$ of NAV in-flight per bridge — a POLICY default. Bridges were the dominant 2022 theft category ($2B across 13 hacks; 69% of 2022 stolen funds), so caps must reflect tail-loss tolerance, not a universal constant. Not a claim about the 2025-26 threat mix — see the currency caveat above. | Chainalysis 2022-08-02; per-incident Chainalysis posts |
| In-Flight Accounting | The engine reads `current_inflight_usd` and never writes it. The caller MUST book executed transfers back into the profile before the next evaluation, and must book unconfirmed/timed-out transfers as outstanding rather than as no-ops. Without this the per-bridge cap is enforced only against a stale balance. | Engine requirement |
| Threshold Comparison | Risk gates compare exact values; rounding is for reporting only. A threshold finer than the display precision must still bite. | Engine requirement |
| De-Peg Halt Threshold | Default: de-peg $\ge 1.0\%$ halts new routing of the wrapped asset, systemically (no reroute). Calibrate to the asset's normal trading band. | Engineering default |
| Finality SLA | Default 120 min. Canonical rollup L1 withdrawals take days: Optimism documents a "minimum 7-day wait" (10,080 min) on the Standard Bridge, while Arbitrum documents a 6.4-day assertion challenge period plus a seven-day canonical-withdrawal safeguard — near, but not identical, so do not hard-code one shared constant. Third-party fast bridges bypass the window by trusting an intermediary. L2-native finality is a separate figure (~2 s soft / ~15-30 min hard on OP Stack). Set the SLA per transfer path, not per protocol name. | docs.arbitrum.io; docs.optimism.io |
| Audit gate | Bridges should have multiple independent audits with no unresolved criticals before meaningful allocation; the engine's scalar `audit_score_pct` + configurable floor is a coarse proxy for that diligence, not a replacement. | Engineering default; see related skill `smart-contract-audit-requirements-before-defi-integration` |
| Feed hygiene | Price inputs MUST be positive and finite; zero/NaN feeds are data failures that raise, never silently report parity. | Engine requirement |
