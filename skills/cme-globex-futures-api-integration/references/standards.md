# CME Globex Order Entry Reference

All values below were verified against CME Group and CME Client Systems Wiki
documentation on 2026-08-21. CME changes Operator ID character policy, price band
values and protection points by advisory notice and by daily product file — re-verify
before relying on any number here.

## Operator ID / Tag 50 — CME Rule 576

| Item | Requirement | Source |
|---|---|---|
| Rule | Rule 576, "Identification of Globex Terminal Operators" — each Globex terminal operator must be identified by a unique Tag 50 ID | [CME Group Rule 576](https://www.cmegroup.com/rulebook/files/cme-group-Rule-576.pdf) |
| Length | Between **2 and 18 characters** | [CME Operator ID Requirements — Registration and Requirements](https://www.cmegroup.com/education/courses/market-regulation/cme-globex-operator-id-requirements/cme-globex-tag-50-id-requirements-registration-and-requirements.html) |
| Characters | Alphanumeric **strongly encouraged**; only a specific list of non-alphanumeric characters is permitted. CME's Operator ID registration documents `_ - : @`. The list has been narrowed before — an amendment effective 1 April 2019 removed characters to reduce downstream processing | [Registering Operator ID / Tag 50](https://www.cmegroup.com/tools-information/webhelp/cme-customer-center/Content/tag50-register.html) |
| Case | **Not case sensitive.** Uniqueness may not be achieved by case alone | CME Operator ID Requirements (above) |
| Uniqueness | Unique at the Clearing Member (Registered Entity) and trading firm level | [Registering and Managing Globex Operator IDs](https://www.cmegroup.com/tools-information/webhelp/cme-customer-center/Content/tag50.html) |
| Manual entry | The ID must be unique to the individual entering the order | CME Rule 576 (above) |
| ATS entry | The ID must be unique to the person, or identified team of persons on the same shift, responsible for operating the ATS. A team/ATS ID may submit **automated messages only** | CME Rule 576; [Eventus, Tag 50 operator ID rules](https://www.eventus.com/cat-article/tag-50-us-future-exchanges-operator-id-rules/) |
| Registration | Registered and maintained in the Exchange Fee System (EFS); the value submitted on every message must **exactly match** the registered ID | CME Operator ID Requirements (above) |
| Field | Tag 50 `SenderSubID` on FIX-based iLink sessions; the `SenderID` field on iLink 3 SBE messages — "Operator ID. Should be unique per Firm ID". Invalid values are rejected | [OnixS iLink 3 `NewOrderSingle514`](https://ref.onixs.biz/cpp-cme-ilink3-handler-guide/structOnixS_1_1CME_1_1iLink3_1_1Messaging_1_1NewOrderSingle514.html) |

The iLink 3 SBE field is identified by **name** (`SenderID`), and sources disagree on the
tag number behind it: the OnixS tag-based mapping above shows tag 50, while
`cme-stp-fix-and-ilink2-tag-value-encoding` documents it as tag 5392, String(20). Nothing in this
skill depends on the number — it validates the value, not the encoding — but confirm it
against the iLink 3 SBE schema before wiring an encoder. Note also that a 20-byte field
width does not relax the Rule 576 limit of 18 characters.

The permitted-symbol list is the one item here most likely to be stale, which is why
the engine takes it as `permitted_operator_id_symbols` rather than hard-coding it.
Confirm the list in the Market Regulation Advisory Notice in force for your firm.

## Manual Order Indicator / Tag 1028 — CME Rule 536.B.

| Item | Requirement | Source |
|---|---|---|
| Values | `Y` = order entered manually by a human; `N` = generated and/or routed without direct human interaction. Anything a computer system or execution algorithm produces is `N` | [CME Rule 536.B. Advisory — Tag 1028](https://www.cmegroup.com/rulebook/files/cme-group-Rule-536-B-Tag1028.pdf) |
| Requirement | Required on applicable iLink order entry messages since June 2011. Messages in scope **without tag 1028, or with an invalid value, are rejected** | CME Rule 536.B. Advisory (above); [Point of Order Origination](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457095240/Point+of+Order+Origination) |
| Audit trail | Part of the minimum acceptable audit trail data elements systems must capture | CME Rule 536.B. Advisory (above) |

## Market with Protection

| Item | Behaviour | Source |
|---|---|---|
| Buy protection limit | Best **offer** + protection points | [iLink Order Types / Order Types for Futures and Options](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457087412/Order+Types+for+Futures+and+Options) |
| Sell protection limit | Best **bid** − protection points | Same |
| Residual quantity | Quantity not filled inside the protected range "remains in the order book as a limit order at the limit of the protected range" | Same |
| Protection points | Published per product; usually about **half the product's non-reviewable range**. Not derived from the price band | Same |
| Market-Limit | A distinct order type: executes at the best price available, residual rests at that price | Same |

## Price banding

| Item | Behaviour | Source |
|---|---|---|
| Range | Price Band Variation Range (PBVR) = Banding Reference Price (BRP) ± Price Band Variation (PBV) | [Limits and Banding](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317722/Limits+and+Banding) |
| PBV | A **static** per-product value, applied symmetrically to both sides to form the range | Same |
| Direction | Rejects **buy** orders above BRP + PBV and **sell** orders below BRP − PBV. It "does not prevent traders from entering bids below the market or entering offers above the market" | Same |
| Reference price | Last transaction; else best bid/offer through the last transaction; else settlement price. During pre-open the settlement price, then the Indicative Opening Price once calculated | Same; [What are Price Limits and Price Banding?](https://www.cmegroup.com/education/courses/introduction-to-futures/price-limits-price-banding) |
| Recalculation | PBVR is recalculated on each price change and the new range applied | Same |
| Scope | Applied to price-based orders. CME's banding documentation does not state that a market order's computed protection limit is band-checked, so this module flags rather than rejects that case | Same |

## Where the per-product numbers live

| Parameter | Authoritative source |
|---|---|
| Tick size, Price Band Variation, protection points | [CME Globex Product Reference Sheet](https://www.cmegroup.com/globex/files/globex-product-reference-sheet.xls) |
| Non-reviewable ranges (Rule 588.H.) | [588.H Globex Non-Reviewable Trading Ranges](http://www.cmegroup.com/rulebook/files/588h-globex-non-reviewable-trading-ranges.xls) |

Any tick, band or protection value appearing in this skill's code, tests or workflows
is **illustrative**. Load the real values per product from the files above.

## Engineering standards enforced by this skill

| Standard | Rule |
|---|---|
| Rule 576 | Reject locally, before transmission, any order whose Operator ID is not 2–18 characters of alphanumerics plus the permitted symbol set, with no whitespace. |
| Rule 536.B. | Refuse to assemble a message unless Tag 1028 is stated explicitly. Never default it. |
| ATS pairing | A team/ATS-registered Operator ID may not carry Tag 1028 = `Y`. |
| Price banding | Check one side only, per order side. Never reject a passive order on the unconstrained side. |
| Tick conformance | Reject an off-tick limit price; round a computed protection limit toward the market. |
| Numerics | Tick divisibility in `Decimal`, never float modulo. Reject non-finite market inputs explicitly rather than letting NaN comparisons surface as a band breach. |
