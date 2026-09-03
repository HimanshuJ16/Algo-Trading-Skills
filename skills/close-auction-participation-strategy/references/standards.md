# Standards for Closing Auction Participation

## US Equity Closing-Auction Timing (all times US/Eastern)

| Gate | Nasdaq Closing Cross | NYSE Closing Auction |
|---|---|---|
| Imbalance dissemination begins | 15:50 (every 10s to 15:55, then every 1s) | 15:50 (every 1s when changed) |
| Near / Far indicative clearing price published | from 15:55 only | from 15:50 (continuous-book clearing price / closing-only interest price) |
| New MOC entry | before 15:55 | until 15:50 |
| New LOC entry | before 15:58 (re-price risk from 15:55) | until 15:50 |
| Entry during the freeze | not applicable | 15:50–16:00, contra side of a published MOC/LOC Significant Imbalance only |
| Cancel / modify on-close orders | before 15:50 | before 15:50 |
| Cross | 16:00 | 16:00 |

Sources:

- Nasdaq, *The Nasdaq Opening and Closing Crosses — Frequently Asked Questions*
  (2025), Q7, Q8, Q10 "On-Close Orders", Q17, Q20, Q25 —
  <https://www.nasdaqtrader.com/content/productsservices/trading/crosses/openclose_faqs.pdf>
- Nasdaq Equity Rule 4754 (Nasdaq Closing Cross), as amended by SR-NASDAQ-2018-052
  (SEC Release 34-84454, approved October 2018), which extended the LOC cutoff to
  15:58 and introduced re-pricing of late LOC orders to the First Reference Price.
- Nasdaq, *TotalView-ITCH 5.0 Specification*, §1.6 Net Order Imbalance Indicator —
  <https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf>
- NYSE, *NYSE opening and closing auctions* fact sheet (2024), "Closing order types"
  and "Closing timeline" —
  <https://www.nyse.com/publicdocs/nyse/markets/nyse/NYSE_Opening_and_Closing_Auctions_Fact_Sheet.pdf>
- NYSE Rule 7.35B (Closing Auction; Closing Auction Imbalance Freeze).

## NOII Field Semantics (ITCH 5.0 §1.6)

| Field | Meaning |
|---|---|
| Paired Shares | Shares eligible to be matched at the Current Reference Price (includes IO orders). |
| Imbalance Shares | Shares that would remain unexecuted at the Current Reference Price. |
| Imbalance Direction | `B` buy imbalance, `S` sell imbalance, `N` no imbalance, `O` insufficient orders to calculate, `P` paused. |
| Far Price | Hypothetical clearing price for **cross-eligible orders only** (the auction book). |
| Near Price | Hypothetical clearing price for **cross plus continuous** orders. |
| Current Reference Price | The price at which the paired/imbalance share counts are computed. |
| Cross Type | `O` opening, `C` closing, `H` halt/IPO, `A` Extended Trading Close. Only `C` is in scope. |

The ITCH price fields are unsigned fixed-point integers with **no documented
sentinel for "not available"**. Because Nasdaq does not disseminate Near/Far
prices for the closing cross before 15:55, a parser will read `0` in those fields
until then. This skill therefore treats any non-positive near/far price as
*absent*, never as a price.

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Cutoff enforcement | The system MUST enforce the **venue's** entry cutoff against the intended *submission* time (not the feed timestamp), with an explicit latency buffer. A configured cutoff override MUST only ever tighten, never loosen, the venue rule. |
| Timezone handling | Timestamps MUST be timezone-aware and converted to `America/New_York`. Naive datetimes MUST be rejected. |
| Cancel/modify freeze | The system MUST treat on-close orders as irrevocable from 15:50 ET and size them accordingly. |
| Price protection | Liquidity-providing orders MUST be LOC or IO (limit-priced). An unpriced MOC has no protection against a cross print dislocated by the imbalance. |
| Indicative price validity | An order MUST NOT be priced off a non-positive or not-yet-disseminated indicative clearing price. |
| Participation cap | Participation SHOULD be capped both as a fraction of the imbalance and as a fraction of predicted auction volume (paired + imbalance). The 10% / 15% defaults in this skill are conservative engineering defaults, **not** a regulatory limit. |
