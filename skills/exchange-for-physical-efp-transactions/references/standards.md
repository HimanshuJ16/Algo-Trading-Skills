# Standards — exchange-for-physical-efp-transactions

An Exchange for Physical (EFP) is one type of **Exchange for Related Position (EFRP)**.
The requirements below are **venue rules**, not a single global standard: every figure is
attributed to the venue that publishes it, and none of them transfers to another venue by
default. Verify the rule text and any product-level guidance for the contract you are
actually trading before wiring a threshold into code.

## Structural requirements (verified against primary sources)

| Requirement | What the rule says | Source |
|---|---|---|
| Opposite-side legs | "An EFRP Transaction shall consist of two discrete but related simultaneous transactions in which one party must be the buyer of (or the holder of the long market exposure associated with) the related position and seller of the corresponding Exchange contract, and the other party … must be the seller of … the related position and the buyer of the corresponding Exchange contract." | ICE Futures U.S. Rule 4.06(b)(i) |
| Quantity equivalence | The related position must involve the underlying commodity (or a by-product or related product) "in a quantity that is **approximately equivalent** to the quantity covered by the Exchange Futures Contract or Option." Identical quantities are not required; appropriate hedge ratios may be used to establish equivalence. A mismatch "more than de minimis … will be scrutinized by the Exchange and, upon request, the parties involved must be able to sufficiently explain and document the bona fide need for the structure." | ICE Futures U.S. Rule 4.06(b)(i); ICE EFRP FAQ; CME Rule 538.C |
| Bona fide transfer | "Each EFRP requires a bona fide transfer of ownership of the Cash Commodity between the parties or a bona fide, legally binding contract between the parties consistent with relevant market conventions for the particular related product transaction." | ICE Futures U.S. Rule 4.06(b)(ii); CME Rule 538 |
| Transitory EFRPs prohibited | Execution "may not be contingent upon the execution of another EFRP or related position transaction between the parties where the transactions result in the offset of the related position without the incurrence of market risk that is material in the context of the related position transactions." Narrow, product-specific exceptions exist (ICE permits immediately offsetting FX EFPs and certain IBA London Gold/Silver Auction EFPs; CME's exception is likewise limited to specified FX cases). | ICE Futures U.S. Rule 4.06(b)(iii) and (b)(vi); CME Rule 538.K |
| Account independence | The accounts must be "(A) independently controlled with different beneficial ownership; or (B) independently controlled accounts of separate legal entities with the same beneficial ownership; or (C) independently controlled accounts within the same legal entity, provided that the account controllers operate in separate business units." | ICE Futures U.S. Rule 4.06(b)(iv); CME Rule 538.B |
| Eligible related position | The cash/OTC component must be the underlying commodity, a by-product, a related product, or an OTC derivative with a reasonable degree of price correlation; it may **not** be a futures contract or an option on a futures contract. | CME Rule 538.C; ICE EFRP FAQ |

## Submission timing — venue-specific, and not what a "30-minute rule" would suggest

There is **no single 30-minute EFRP reporting deadline**. Prior versions of this skill
stated one; it is not supported by either venue's published requirements.

| Venue | Deadline | Source |
|---|---|---|
| CME Group | Submit "as soon as possible" following execution, and no later than the **end of the business day** on which the EFRP was executed. Submission is via CME Direct or CME ClearPort. | CME Group EFRP guidance / Block and EFRP quick reference |
| ICE Futures U.S. — energy products | Submit through ICE Block as soon as possible after the parties agree terms, and no later than the **end of the trading session** for the corresponding contract market, absent mitigating circumstances. | ICE Futures U.S. EFRP FAQ, Q17 |
| ICE Futures U.S. — all other products | As above, but no later than **30 minutes after the end of the trading session** for the corresponding contract market. | ICE Futures U.S. EFRP FAQ, Q17 |
| ICE Futures U.S. — executed outside normal trading hours | Report no later than **5 minutes after the open of the next trading session** for the applicable contract. | ICE Futures U.S. EFRP FAQ, Q17 |

Note the shape of the ICE deadlines: they run from the **end of the session**, not from
the moment of execution. A control that starts a 30-minute timer at execution enforces a
deadline no venue actually sets.

## Recordkeeping

- Parties must maintain all documents relevant to both legs — documents evidencing title,
  or the contracts to buy or sell the cash commodity, and master swap agreements where
  applicable — and furnish them to the Exchange on request; the carrying clearing member is
  responsible for providing them on a timely basis (ICE Rule 4.06(b)(v)).
- ICE additionally requires records per CFTC Regulation 1.35; where an EFRP is not entered
  into ICE Block immediately, the date and time of execution must be captured on a separate
  record with the other trade details (Exchange Rule 6.08), and EFRP volume must appear in
  the daily large trader position file (Exchange Rule 6.15).
- CME may request negotiation records — emails, instant messages, recorded audio — where
  they exist.

## Quantity tolerance: worked venue example

Eurex publishes an explicit numeric tolerance for one product family: for FX Futures EFPs,
"the nominal value of the opposite FX transaction shall … be equivalent to the nominal
value of the FX Futures contract and shall not deviate from it by more than 20 percent."
Eurex EFP-I trades carry a different constraint again (the cash basket must comprise at
least ten index components or equities representing at least half the index, with at least
20% of nominal value in index components). Neither figure applies at CME or ICE. Use it as
evidence that a tolerance must be configured per venue and product, not as a default.

Eurex off-book EFP trades (EFP-F, EFP-I, EFS) are governed by the *Conditions for Trading
at Eurex Deutschland* (Number 4.3) together with the contract specifications, chapter
3.2.2 for interest-rate derivatives and credit index futures and chapter 3.2.3 for equity
index and FX futures. Earlier versions of this skill cited a "Eurex Rule 4.6"; no such
provision was located, and the reference has been removed.

## Basis arithmetic

Cost-of-carry relation used for the fair basis:

$$F = S\,e^{(r + u - y)T} \quad\Longrightarrow\quad \text{fair basis} = S\left(e^{(r+u-y)T} - 1\right)$$

with $r$ the continuously compounded financing rate, $u$ the proportional annual storage
cost, and $y$ the convenience yield (dividend yield for an equity or index underlying), all
on the same day-count basis as $T$ (Hull, *Options, Futures, and Other Derivatives*,
determination of forward and futures prices). For a consumption commodity the relation is
an upper bound rather than an equality: only the rich side is arbitrage-enforceable, since
the physical generally cannot be borrowed and sold short. See
`commodity-futures-storage-and-carry-cost-modeling` for the full treatment, including
per-unit storage costs and implied convenience yield.

## Sources

- ICE Futures U.S., Trading Rules, Rule 4.06 — <https://www.ice.com/publicdocs/rulebooks/futures_us/4_Trading.pdf>
- ICE Futures U.S., EFRP FAQs (9 February 2026) — <https://www.ice.com/publicdocs/futures_us/EFRP_FAQ.pdf>
- CME Group, Rule 538 (Exchange for Related Positions) — <https://www.cmegroup.com/rulebook/files/cme-group-Rule-538.pdf>
- CME Group, Market Regulation Advisory Notice RA2306-5, Rule 538 EFRP — <https://www.cmegroup.com/content/dam/cmegroup/notices/market-regulation/2023/12/CME-Group-RA2306-5-Rule-538-EFRP-12-7-2023.pdf>
- CME Group, Exchange for Related Positions (EFP/EFR/EOO) — <https://www.cmegroup.com/solutions/clearing/operations-and-deliveries/efp-efr-eoo-trades.html>
- Eurex, Exchange for Physicals (T7 Entry Services) — <https://www.eurex.com/ex-en/trade/eurex-t7-entry-services/exchange-for-physicals>
- Eurex, Conditions for Trading at Eurex Deutschland — <https://www.eurex.com/resource/blob/337108/a6f145be73c8b2061eb69d6ddc9bb9a1/data/2025_12_01_eurex_d_handelsbedingungen_en.pdf>
