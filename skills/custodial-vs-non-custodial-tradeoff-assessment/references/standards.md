# Standards for Custodial vs Non-Custodial Trade-Off Assessment

## Engineering standards enforced by `scripts/`

| Metric | Engineering Standard |
|---|---|
| Mandate Constraints Are Pass/Fail | Key sovereignty, counterparty-exposure tolerance, latency floor and qualified-custodian requirements MUST disqualify an architecture, never merely reduce its score. |
| Tolerance Direction | `max_counterparty_risk_pct` is a tolerance BUDGET. A lower value MUST admit fewer architectures, never score exposed ones more favourably. |
| Latency Floor Realism | On-chain architectures MUST carry a latency floor equal to block inclusion time, not network round-trip. |
| Fail Closed | If no architecture survives the hard constraints, the engine MUST raise rather than return the least-bad disqualified option. |
| Input Validation | NaN, infinite, negative and out-of-range inputs MUST be rejected. A NaN latency budget silently passes every `<=` comparison. |
| Deterministic Ranking | Ties MUST break on a declared rule (lower residual exposure, then name), not on declaration order. |
| Auditable Weights | Composite weights MUST be caller-supplied, validated to sum to 1.0, and returned on the report. |
| Illustrative Figures Flagged | Default risk and cost figures are placeholders and MUST be flagged as such in the output until replaced by firm-specific due diligence. |
| Off-Exchange Settlement Review | Monthly volume at or above a firm-policy threshold (default \$10M) SHOULD trigger an off-exchange settlement review before committing balances to a custodial venue. This is a repo default, **not** a regulatory threshold. |

## Verified external facts

### Execution latency floors

| Claim | Source | Relevance |
|---|---|---|
| Ethereum mainnet slot time is 12 seconds; finality under Gasper averages roughly 16 minutes. EIP-7782 ("Reduce Block Latency") proposes shortening slots. | [EIP-7782](https://eips.ethereum.org/EIPS/eip-7782) | Sets the 12,000 ms latency floor for L1 non-custodial execution. Override for L2s or faster chains. |
| Centralized matching engines operate in the microsecond-to-millisecond range; DEX execution is bounded by block inclusion, which is not guaranteed. | Secondary/industry sources; no single authoritative venue SLA located | The order-of-magnitude gap is well established; a specific "≤1 ms" round-trip figure is NOT asserted, since published sub-millisecond numbers refer to colocated matching-engine time, not client round-trip. |

### Off-exchange settlement

| Claim | Source | Relevance |
|---|---|---|
| Fireblocks Off-Exchange holds assets in Collateral Vault Accounts — on-chain MPC wallets "mutually controlled" by trader and exchange — that programmatically lock and mirror assets to the exchange account, with on-chain settlement rebalancing as positions move. | [Fireblocks Off-Exchange](https://www.fireblocks.com/platforms/off-exchange/) | Justifies `key_control=SHARED_OR_CUSTODIAN`: co-control is **not** sole key control, so it does not satisfy a sovereignty mandate. |
| The arrangement protects principal from venue bankruptcy but **maintains settlement exposure during active trading positions** — it mitigates rather than eliminates counterparty risk. | [Fireblocks Off-Exchange](https://www.fireblocks.com/platforms/off-exchange/) | Justifies a non-zero `residual_counterparty_exposure_pct` for the hybrid model. |
| Copper ClearLoop delegates assets to an exchange without an on-chain transaction; assets never leave Copper's custody and trades settle on Copper's infrastructure. A dedicated account structure protected by an English Law Trust provides bankruptcy-remote protection, with Copper's risk team ensuring the exchange maintains sufficient collateral at Copper. | [Copper ClearLoop](https://copper.co/en/products/clearloop) | Confirms the risk is *substituted* (custodian and trust structure) rather than removed. |

### MEV mitigation

| Claim | Source | Relevance |
|---|---|---|
| Flashbots Protect routes transactions through a private mempool where they are hidden from frontrunning and sandwich bots. It does **not** guarantee elimination of MEV — it offers refunds where MEV occurs, inclusion is not guaranteed, and the user relies on Flashbots as an intermediary. | [Flashbots Protect docs](https://docs.flashbots.net/flashbots-protect/overview) | The mitigation text says "mitigates rather than eliminates". Claiming prevention would be an overclaim. |

### Regulatory context (jurisdiction-specific; assert, do not assume)

Custody is a licensed activity in most major jurisdictions, and whether an entity
*may* self-custody is a legal question this module does not answer. It accepts a
`regulatory_constraint` the caller asserts. Nothing here is legal advice.

| Jurisdiction | Position | Source |
|---|---|---|
| **EU** | MiCA Article 75 has applied since **30 December 2024**. Custody is defined as safekeeping or controlling crypto-assets, or the means of access to them (private keys), on behalf of clients. CASPs must conclude a client agreement, keep a per-client position register, segregate client assets from their own, and not reuse them for their own account. Art 75(8) caps CASP liability at the market value of the lost crypto-asset at the time of loss. Outsourced custody is permitted only to entities licensed under Art 59. | [ESMA MiCA rulebook](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica) |
| **US** | The SEC's proposed **Safeguarding Advisory Client Assets** rule (File S7-04-23, proposed March 2023), which would have replaced the Advisers Act custody rule and expanded coverage to crypto assets, was **formally withdrawn on 12 June 2025** as one of fourteen withdrawn proposals. Advisers Act Rule 206(4)-2 remains the operative custody rule. Do not cite the Safeguarding Rule as a requirement. | SEC rulemaking S7-04-23; withdrawal reported by [Proskauer](https://www.proskauer.com/alert/sec-withdraws-fourteen-rule-proposals) |
| **US** | SEC Division of Investment Management **no-action letter dated 30 September 2025** states the staff would not recommend enforcement where registered advisers and registered funds use state trust companies as crypto custodians, subject to conditions (annual due diligence on state authorisation, audited financials and internal-control reports, a written agreement barring lending or pledging without consent, segregation, and disclosure). This is a **staff position, not a Commission rule**, and is not binding law. | Reported by [Morgan Lewis](https://www.morganlewis.com/pubs/2025/10/crypto-custody-breakthrough-sec-staff-grants-relief-for-registered-funds-advisers) |

**Currency note.** The SEC's 2026 Regulatory Agenda has been reported to signal a
revival of Custody Rule changes. Treat the US position above as current-as-checked
(August 2026) and re-verify before relying on it.
