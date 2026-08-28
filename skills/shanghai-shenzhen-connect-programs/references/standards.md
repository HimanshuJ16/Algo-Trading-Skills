# Standards for Shanghai-Shenzhen Connect (Northbound Trading)

Primary source: **HKEX, *Information Book for Investors — Stock Connect*, Version
Date 6 July 2026** —
<https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Getting-Started/Information-Booklet-and-FAQ/Information-Book-for-Investors/Investor_Book_En.pdf>.
Section numbers below are that document's. Quoted text is verbatim.

These are market-operator programme rules made by SEHK jointly with SSE and SZSE,
plus PRC rules on foreign shareholding that the programme enforces. They bind a
participant trading Northbound; they are not general securities regulation and
carry no force outside Stock Connect. Companion documents: *Information Book for
Market Participants (EP/CP)* and the SSE/SZSE trading rules the programme
inherits.

## Quota — §3.4

| Item | Standard | Source |
|---|---|---|
| Daily Quota | "The Northbound Daily Quota is set at RMB 52 billion for each of Shanghai Connect and Shenzhen Connect." Southbound is RMB 42 billion per channel. | §3.4 |
| Scope | "Trading of A shares and ETFs shares the same daily quota." | §3.4 |
| Aggregate Quota | None. Abolished for Shanghai Connect on 16 August 2016; never introduced for Shenzhen Connect. | §3.4 |
| Basis | "The Daily Quota is applied on a 'net buy' basis. Based on that principle, investors are always allowed to sell their cross-boundary securities regardless of the quota balance." | §3.4 |
| Balance formula | `Daily Quota Balance = Daily Quota – Buy Orders + Sell Trades + Adjustments` | §3.4 |
| Reset | "The Daily Quota will be reset every day. Unused Daily Quota will NOT be carried over to next day's Daily Quota." | §3.4 |
| Opening call auction exhaustion | "If the Northbound Daily Quota Balance drops to zero or the Daily Quota is exceeded during the opening call auction session, new buy orders will be rejected. However, as order cancellation is common during opening call auction, the Northbound Daily Quota Balance may resume to a positive level before the end of the opening call auction. When that happens, SEHK will again accept Northbound buy orders." | §3.4 |
| Continuous / closing auction exhaustion | "Once the Northbound Daily Quota Balance drops to zero or the Daily Quota is exceeded during a continuous auction session, no further buy orders will be accepted for the remainder of the day. The same arrangement applies to the closing call auction." | §3.4 |
| Accepted orders survive | "buy orders already accepted will not be affected by the Daily Quota being used up and will remain on the order book of SSE and SZSE respectively unless otherwise cancelled by relevant SEHK Participants." | §3.4 |
| Dissemination | Real-time monitoring by SEHK; published on the HKEX website every minute and via SCM of OMD-C at 5-second intervals **only once the balance falls below 30%**, otherwise shown as "Available" / a null value. | §3.4 |

**Two asymmetries the formula encodes and a naive implementation loses.** Quota is
consumed by buy **orders** at submission but restored by sell **trades** at
execution. And "the Daily Quota is exceeded" is a state the programme explicitly
contemplates, so the balance can go negative: the order that exhausts the quota is
accepted, and it is the *next* one that is refused.

The source does not state that the balance is capped at the Daily Quota, and the
"net buy" basis implies it is not — a net-sell day credits more than it consumed.
The `Adjustments` term's triggers are not enumerated in this document.

## Order rules — §3.8, §3.9, §3.10, §3.11

| Item | Standard | Source |
|---|---|---|
| Order types | "For Northbound trading, only limit orders (i.e. orders which can be matched at the specified price or a better price) will be accepted for SSE Securities and SZSE Securities throughout the day." | §3.8 |
| Price limit | "±10% price limit for stocks traded on SSE/SZSE Main Board; and a ±20% for stocks traded on SSE STAR Market and SZSE ChiNext Market", based on the previous closing price. "Any order with a price beyond the price limit will be rejected by SSE or SZSE. The upper and lower price limit will remain the same intra-day." | §3.9 |
| ETF price limit | "±10% for ETFs traded in SSE/SZSE under normal circumstances, and a ±20% for some ETFs as specified by SSE/SZSE." The ±20% set is a published list, not derivable from the code. | §3.9 |
| Delisting/relisting | No price limit on the first day of delisting or relisting; dynamic price limit and intraday suspension per SSE/SZSE rules. | §3.9 |
| Dynamic price check | SEHK rejects, via CSC, a buy order priced below the current best bid (or last traded price, or previous close) "beyond a prescribed percentage" — "set at 3% during the initial phase and may be adjusted from time to time". Applied from the 5-minute input period before the opening call auction until market close. | §3.10 |
| Stock code | 6 digits; SSE/SZSE codes must be used when placing orders. | §3.11 |
| Board lot | "SSE and SZSE Securities are subject to the board lot size of 100 shares or units (except for STAR shares whose board lot size is 1 share with minimum order size of 200 shares). Buy orders must be in board lots." | §3.11 |
| Odd lots | "Odd lot trading is only available for sell orders and all odd lots should be sold in one single order." Board lot and odd lot orders match on the same platform at the same price, unlike Hong Kong. | §3.11 |
| Maximum order size | "1 million shares or units (300,000 shares for stocks on SZSE ChiNext Market and 100,000 shares for stocks on SSE STAR Market)". | §3.11 |
| Tick size | "uniformly set at RMB 0.01 for A shares and RMB 0.001 for ETFs". | §3.11 |
| No block trades | "For Northbound trading, block trade facility is not available." | §3.13 |
| No manual trades | "For Northbound trading, there is no manual trade facility." | §3.14 |

**ChiNext is not excepted from the 100-share board lot.** Only STAR is. ChiNext
shares STAR's ±20% price limit and its investor eligibility restriction, which
makes the board-lot exception easy to over-generalise; §3.11 names STAR alone.

## No day trading, and the mechanism that enforces it — §3.12, §3.19

| Item | Standard | Source |
|---|---|---|
| No day trading | "Day trading is not allowed for both Connect Markets. Therefore, Hong Kong and overseas investors buying SSE and SZSE Securities on T-day can only sell the shares on and after T+1 (see also Pre-trade Checking)." Southbound, by contrast, permits day trading. | §3.12 |
| Pre-trade checking | "Under SEHK's pre-trade checking model, sell orders will be rejected if the cumulative sell quantity for the day is higher than the SEHK Participant's shareholding position at market open." | §3.19 |
| Pre-delivery | Shares held with another participant or custodian must be transferred to the selling participant on T-1 in order to sell on T day, unless an SPSA is in place. | §3.19 |
| SPSA | Introduced 30 March 2015. CCASS "will take a snapshot of the Connect Securities holdings under each SPSA [...] and replicate such holdings to CSC to perform pre-trade checking." Each SPSA has a unique Investor ID; at most 20 designated executing brokers. | §3.19 |

The T+1 prohibition is not enforced by comparing dates on a position. It is
enforced by the *market-open* position: shares bought today are not in that
snapshot, so they cannot be sold today. Implementing the rule as a
`purchase_date == today` comparison enforces a weaker condition and misses
overselling entirely.

The document does not state explicitly whether a cancelled sell order releases
its share reservation from "the cumulative sell quantity for the day". Confirm
against the CSC specification before relying on the release behaviour.

## Foreign shareholding restrictions — §3.20

| Item | Standard | Source |
|---|---|---|
| Single investor | A single foreign investor's shareholding in a listed company "is not allowed to exceed 10% of the company's total issued shares", counted across all channels including QFII, RQFII and Stock Connect. | §3.20 |
| Aggregate | All foreign investors' shareholding in a company's A shares "is not allowed to exceed 30% of its total issued shares". ETFs are not subject to shareholding restrictions. | §3.20 |
| Disclosure trigger | SSE/SZSE publishes a notice when aggregate foreign shareholding of an A share reaches 24%. | §3.20 |
| Buy suspension | "Once SSE/SZSE informs SEHK that the aggregate foreign shareholding of an SSE/SZSE Security reaches 28%, further Northbound buy orders in that SSE/SZSE Security will not be allowed, until the aggregate foreign shareholding of that SSE/SZSE Security is sold down to 26%." | §3.20 |
| Forced sale | If 30% is exceeded, the foreign investors concerned "will be requested to sell the shares on a last-in-first-out basis within five trading days." Selling is always permitted at any level. | §3.20 |

The 28% suspend / 26% resume pair is hysteresis, not a typo. A control that both
suspends and resumes at 28% would flap across the boundary.

## Eligibility, sessions, calendar, currency, settlement

| Item | Standard | Source |
|---|---|---|
| Investor eligibility | "Except ChiNext Stocks of Shenzhen Stock Exchange (SZSE) and STAR stocks of Shanghai Stock Exchange (SSE) which may only be traded by institutional professional investors, Hong Kong and overseas investors are allowed to trade any Connect Securities". | §2 |
| Security eligibility | Index-constituent and A+H based, excluding shares not traded in RMB and shares under "risk alert" (ST / \*ST companies, delisting or suspended). Reviewed periodically by SEHK. | §3.1–3.2 |
| Sell-only | An eligible stock that subsequently fails the market-cap, turnover or suspension criteria, or is placed under risk alert, "will be designated as a sell-only stock [...] and will be restricted from buying". Eligible ETFs have an equivalent sell-only designation. | §3.1–3.2 |
| Sessions | Opening call auction 09:15–09:25; continuous auction 09:30–11:30 and 13:00–14:57; closing call auction 14:57–15:00. SSE/SZSE accept no order cancellation 09:20–09:25 or 14:57–15:00. | §3.5 |
| Trading calendar | After the April 2023 Trading Calendar Enhancement, Northbound trading is open on a Hong Kong business day "when both the markets in Hong Kong and Mainland are open for trading". A Mainland trading day that is a Hong Kong public holiday is not a Northbound trading day. | §3.6 |
| Trading currency | "Hong Kong and overseas investors trade and settle SSE and SZSE Securities in RMB only." HKSCC settles Northbound trades with CCASS Participants in RMB, and with ChinaClear in RMB. | §3.7, §4.7 |
| Settlement cycle | "stock settlement on T day, and money settlement on T day or T+1 day, as the case may be." ChinaClear debits/credits stock accounts before 6:00pm on T day; HKSCC runs four Northbound batch settlement runs at around 4:45pm, 5:30pm, 6:15pm and 7:00pm on T day. | §4.2 |
| Margin | Hong Kong and overseas investors cannot participate in the SSE/SZSE Margin Trading and Securities Lending programme; SEHK Participants may provide their own securities margin financing subject to SSE/SZSE requirements. | §3.15 |

**RMB, not CNH.** The Information Book says "RMB" throughout and never uses
"CNH". Prices, the Daily Quota and money settlement are all RMB-denominated. A
Hong Kong investor funds the trade from offshore RMB liquidity, but that is a
treasury fact about where the currency is sourced, not the denomination of the
trade — and the CNH/CNY basis is an FX exposure, not a settlement currency
distinction. See `multi-currency-pnl-and-fx-conversion`.

## Out of scope

Southbound trading (a different quota, different order types, and day trading
*is* permitted), the Bond Connect and ETF Connect programmes, Northbound Investor
ID reporting, disclosure of interests obligations, corporate-action handling,
taxation, and the eligibility-list maintenance process itself. The Northbound
eligible-security and sell-only lists are published by SEHK and must be ingested
as data; they cannot be derived from a stock code.
