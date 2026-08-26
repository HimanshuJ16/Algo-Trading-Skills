# Standards for Meta-Strategy Signal Arbitration

## Engineering standards

These are design standards for this module, not regulatory mandates.

| Metric | Engineering Standard |
|---|---|
| Risk Veto Precedence | Risk-off stop loss signals MUST take absolute precedence over alpha signals, independent of the vetoing strategy's allocation weight. The veto MUST report a flat target ($0.0$), never a maximum-conviction short ($-1.0$). |
| Internal Netting | Opposing sub-strategy orders for the same symbol MUST be netted prior to market routing. |
| Fail-Closed Validation | Malformed input MUST raise rather than emit an order, and MUST NOT be swallowed by the caller in favour of the sub-strategies' raw orders. Unrecognised `strategy_id`s MUST NOT receive a fallback weight. |
| Symbol Isolation | Every signal in a batch MUST carry the arbitrated symbol; cross-symbol netting is a defect, not a feature. |
| Deadband Suppression | Signal changes below the deadband threshold ($\epsilon_{\text{deadband}}$) MUST NOT trigger rebalancing. A suppressed pass MUST report zero netting savings, since no order was routed. |
| Savings Attribution | `internal_netting_savings_usd` MUST reflect only cost avoided on an order actually routed, so that summing the field across passes yields a defensible TCA figure. |

## Regulatory touchpoints (jurisdiction-specific — verify applicability)

Self-match exposure, not transaction cost, is the primary reason opposing internal orders must not both reach the venue. Neither provision below mandates internal netting; both bear on it.

| Jurisdiction | Source | What it actually says | Status |
|---|---|---|---|
| US equities | FINRA Rule 5210, Supplementary Material .02 (Self-Trades) | "Transactions resulting from orders that originate from unrelated algorithms or separate and distinct trading strategies within the same firm would generally be considered bona fide self-trades." Members must have "policies and procedures in place that are reasonably designed to review their trading activity for, and prevent, a pattern or practice of self-trades resulting from orders originating from a single algorithm or trading desk, or related algorithms or trading desks." | Mandatory (the policies-and-procedures obligation). Amended effective 2017-04-03 (SR-FINRA-2017-004). |
| US futures (CME/CBOT/NYMEX/COMEX) | Rule 534 ("Wash Trades Prohibited") and its Market Regulation Advisory Notice | Rule 534: "No person shall place or accept buy and sell orders in the same product and expiration month ... where the person knows or reasonably should know that the purpose of the orders is to avoid taking a bona fide market position exposed to market risk." The advisory treats algorithms run by *fully independent* groups with no knowledge of each other's orders as bona fide (the firm "must be able to demonstrate the independence"), but says that "otherwise independent algorithms ... operated and/or controlled by the same individual or team" trading against each other on more than an incidental basis "may be deemed to violate the prohibition", and recommends functionality that minimises self-matching. | Rule 534 is mandatory. Self-Match Prevention (SMP) is explicitly **optional**. No numeric "incidental" threshold is prescribed — do not hard-code one. |

**Implication for this module.** Once sub-strategies feed a common arbitration layer, they are related algorithms under shared control, which is the harder side of both provisions. Netting resolves it by construction: the opposing interest is offset in the firm's own book and never becomes an order. Bypassing the arbitrator to preserve a claim of independence removes the mitigation without restoring the independence.

**Out of scope.** This layer is not venue-level SMP configuration and not a market-access pre-trade risk control; it enforces neither and substitutes for neither.

## Cost-model convention

`estimated_transaction_cost_bps` is a **one-way, all-in** cost per unit of notional. SEC Regulation NMS defines the average effective spread as "double the amount of difference between the execution price and the midpoint of the national best bid and national best offer" (17 CFR 242.600(b)) — so the cost of crossing, measured against mid, is *half* the quoted spread. Passing a full quoted spread overstates savings by roughly $2\times$.

## Sources

- FINRA Rule 5210 (Publication of Transactions and Quotations), Supplementary Material .02 — https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210
- CME Group Market Regulation Advisory Notice on Rule 534, as self-certified to the CFTC (RA1411-5; Rule 534 text and FAQ) — https://www.cftc.gov/sites/default/files/filings/orgrules/14/12/rule121714comexdcm012.pdf
  Q&A text above was verified against this CFTC-filed copy. The currently operative notice in the series is CME Group RA2008-5 (effective trade date 2020-09-17), whose amendment concerned an audit-trail tag reference rather than substantive guidance; confirm the live notice before relying on Q&A numbering.
- 17 CFR 242.600(b), Regulation NMS definitions (average effective spread) — https://www.law.cornell.edu/cfr/text/17/242.600
