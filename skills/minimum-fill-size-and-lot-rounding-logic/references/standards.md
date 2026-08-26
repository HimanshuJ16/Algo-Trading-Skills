# Standards for Minimum Fill Size & Lot Rounding

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Quantity arithmetic | All rounding, remainder, and comparison arithmetic on quantities MUST use exact decimal types. Binary floating point MUST NOT be used: `math.floor(0.29 / 0.01) * 0.01` evaluates to `0.28` and `0.29 % 0.01` evaluates to `0.009999999999999974`. |
| Lot size provenance | `lot_size` and `min_order_quantity` MUST be sourced per **security**, with the source and as-of date recorded. They MUST NOT default to a constant — board lots are per-security and time-varying. |
| Rounding direction | The rounding mode MUST be explicit (`FLOOR` / `CEIL` / `ROUND_HALF_UP`). Language-default `round()` MUST NOT be used for nearest-lot rounding: banker's rounding is asymmetric across ties (250 → 200, 350 → 400 at a lot of 100). |
| Overshoot disclosure | Any rounding that increases the quantity above the request MUST be reported to the caller as a signed delta, so the risk layer can see the added exposure. |
| Odd-lot policy | The odd-lot flag MUST change the routed quantity, not only the audit message. It MUST be configured per venue and per instrument. |
| Notional floor | A minimum-notional check MUST be evaluated on the **post-rounding** quantity. Where no price is available, the check MUST be reported as not performed rather than as passed. |
| FIX Tag 110 | `MinQty` MUST be populated only when the caller explicitly requests a minimum-execution constraint. It MUST NOT be derived from the venue's minimum order size. |
| FIX Tag 1089 | `MatchIncrement` MUST NOT exceed the routed quantity, and MUST NOT be inferred from the board lot. |
| Depth checks | Fill-likelihood checks MUST use measured depth. An unmeasured depth MUST be reported as unknown, never defaulted to a value that makes the check pass. |
| Status vs warnings | Advisory findings MUST accumulate independently of the terminal status, so a depth or overshoot finding cannot be overwritten by a later rounding outcome. |
| Rejection handling | A below-minimum or below-notional rejection is deterministic and MUST NOT be retried unchanged. |

## FIX field semantics (verified against the FIX dictionary)

The two pairs below are routinely conflated. Venue sizing constraints arrive as **reference data**; execution constraints are **instructions the sender attaches to its own order**.

| Tag | Field | Definition | Direction | Source |
|---|---|---|---|---|
| 561 | `RoundLot` | "The trading lot size of a security" | Venue → client, on Security Definition (`d`) / Security List (`y`) | [FIX 4.4 tag 561](https://www.onixs.biz/fix-dictionary/4.4/tagnum_561.html) |
| 562 | `MinTradeVol` | "The minimum trading volume for a security" | Venue → client, on Security Definition (`d`) / Security List (`y`) | [FIX 4.4 tag 562](https://www.onixs.biz/fix-dictionary/4.4/tagnum_562.html) |
| 110 | `MinQty` | "Minimum quantity of an order to be executed." | Client → venue, on the order | [FIX 4.4 tag 110](https://www.onixs.biz/fix-dictionary/4.4/tagnum_110.html) |
| 1089 | `MatchIncrement` | "Allows orders to specify a minimum quantity that applies to every execution (one execution could be for multiple counter-orders)." The order may still fill against smaller orders, but the cumulative execution quantity must be in multiples of the `MatchIncrement`. | Client → venue, on the order | [FIX 5.0 SP2 tag 1089](https://www.onixs.biz/fix-dictionary/5.0.sp2/tagnum_1089.html) |

## Venue behaviour (verified against primary or market-operator sources)

| Venue | Constraint | Documented behaviour | Source |
|---|---|---|---|
| US NMS stocks | Round lot (17 CFR 242.600(b)(93)) | Price-tiered since **3 November 2025**: $0–$250 → 100 shares; $250.01–$1,000 → 40; $1,000.01–$10,000 → 10; above $10,000 → 1. Tier assignment is made "semiannually, based on the NMS stock's average closing price on the primary listing exchange during a one-month Evaluation Period." Quotation sizes are disseminated as the number of actual shares "rounded down to the nearest multiple of the round lot size assigned to the security." | [Nasdaq UTP Vendor Alert #2025-10](https://www.nasdaqtrader.com/TraderNews.aspx?id=UTP2025-10); tiers implemented in this repo by `round_lot_for_nms_price` in `latency-arbitrage-defensive-order-sizing` |
| Nasdaq | Minimum Quantity Order Attribute (Equity 4, Rule 4703(e)) | An order carrying the attribute "may not be displayed"; if it is also marked with a Display Order Attribute "the System will accept the Order but will give a Time-in-Force of IOC, regardless of the Time-in-Force marked by the Participant." An order entered through RASH, QIX or FIX "must have a minimum quantity of one round lot or any multiple thereof, and a mixed lot minimum quantity condition will be rounded down to the nearest round lot." After a partial execution, the minimum quantity value is reduced to the shares remaining. | Nasdaq Equity 4, Rule 4703(e), as reproduced in SEC rule filings; the non-display and no-routing behaviour is restated in [FR Vol. 84 No. 30 (13 Feb 2019)](https://www.govinfo.gov/content/pkg/FR-2019-02-13/html/2019-02115.htm) |
| HKEX Securities Market | Board lot / odd lot | Board lot size is set per security, not per market. An odd lot is "a trade with the quantity of shares less than one board lot which can be concluded through the OTP-C using the operation specified for odd lot transaction" — i.e. it is not auto-matched on the main board. | [HKEX Securities Trading Mechanism](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en) |
| Tokyo Stock Exchange | Trading unit | Trading units for domestic stocks were standardised to a single unit of **100 shares on 1 October 2018**. | [JPX — Standardization of Trading Unit](https://www.jpx.co.jp/english/equities/improvements/unit/01.html) |
| SGX-ST | Board lot | Standard board lot has been 100 units since 2015. From **5 October 2026** the standard board lot falls to 10 units for specified instruments priced above S$10 up to S$100, and to 1 unit above S$100, with quarterly re-evaluation thereafter. | [Allen & Gledhill summary of the SGX response paper](https://www.allenandgledhill.com/sg/publication/articles/33309/sgx-reduces-standard-board-lot-size-and-enhances-custody-structure) |
| Binance Spot | `LOT_SIZE` / `NOTIONAL` filters | `LOT_SIZE`: `quantity >= minQty`, `quantity <= maxQty`, and `quantity % stepSize == 0`. `NOTIONAL`: `price * quantity` must sit between `minNotional` and `maxNotional`, with `applyMinToMarket`/`applyMaxToMarket` controlling whether the bounds apply to market orders. | [Binance Spot API — Filters](https://developers.binance.com/docs/binance-spot-api-docs/filters) |

## Confidence and limitations

- **Nasdaq Rule 4703(e)** — the quoted provisions were verified from SEC rule-filing text and a Federal Register document citing Rule 4703(e); the Nasdaq rulebook itself could not be retrieved directly during this review. Confirm the subsection lettering and current wording against the live rulebook before relying on it in a compliance artefact.
- **SGX 5 October 2026 change** — the price tiers come from a law-firm summary of SGX's response paper, not from the SGX circular text. Confirm the tiers and the affected instrument list against the SGX circular; SGX has stated it will notify participants of each quarter's changes by circular and will purge resting orders in an affected book before a new board lot size takes effect.
- **US odd lots** — odd-lot orders are accepted and executed on US equity venues; what the round lot governs is quotation. Rule 600(b)(16) defines a "bid or offer" in terms of a price for "one or more round lots", so a sub-round-lot quote is disseminated as odd-lot information (Rule 600(b)(69)) rather than standing as a protected quotation. The September 2024 amendments added the best odd-lot order to that definition with a compliance date in May 2026; this review did not retrieve the adopting release directly, so treat the date as indicative and confirm it before citing it in a regulatory filing.
- Round lot tiers, board lots, and exchange filters change. Every figure in this file is a snapshot, not a constant — the engineering standard above (source the value per security, record the as-of date) exists precisely because these tables go stale.

## Cross-references

- Reference-data sourcing for lot sizes: `reference-data-golden-source-designation`
- Price-side equivalent of this constraint: `exchange-tick-size-regime-tracking`
- Parent-order slicing that feeds child quantities into this skill: `execution-algo-twap-vwap-slicing`
- Display-quantity constraints on the same orders: `iceberg-order-native-broker-support-vs-simulation`
- Cost of retrying rejected child orders: `order-to-trade-ratio-fee-penalty-avoidance`
