# Standards for TWSE Centralised-Market Cash Equity Integration

Source of record: the Taiwan Stock Exchange's own **集中市場交易制度介紹 /
Trading Mechanism Introduction** page, in both language editions, together with
the daily **平盤下得融(借)券賣出之證券名單** report (TWT92U) and the TWSE
foreign-investor pages. Transcribed here on **2 September 2026**. Every table
below is TWSE's, not a secondary summary.

| Metric | Engineering Standard |
|---|---|
| Base currency | New Taiwan dollar (NT$). The finest tick is NT$0.01, and NT$0.01 is also the lowest quotable price. |
| Tick size | Order prices MUST be an exact multiple of the tick for the **order price's** band, drawn from the schedule applicable to the instrument's class. Bands read 「X元至未滿Y元」 — the upper bound is **exclusive**. TWSE Operating Rules Art. 62. |
| Daily price limit | ±10% of the day's **auction reference price** (開盤競價基準) since 1 June 2015, raised from 7%. The computed bound is then snapped onto the tick grid **toward** the reference, because the outward tick breaches the band. Art. 63 read with Art. 62. Both bounds inclusive. |
| Trading unit | 1,000 shares/units for stocks, foreign-stock first listings, ETFs, ETNs, REITs, TDRs, closed-end funds and warrants. Secondary listings of foreign stocks and offshore ETFs are 不以1,000股(單位)為限. Bonds trade in NT$100,000 par. Odd lots are 1–999. |
| Trading hours | Order entry 08:30; opening call auction 09:00; continuous trading (逐筆交易) 09:00–13:25; closing call auction 13:25–13:30. Intraday odd lot: orders 09:00–13:30, first match 09:10, then every 5 seconds. After-hours odd lot: orders 13:40–14:30, single auction at 14:30. |
| Order types | Six combinations of price type (limit / market) and duration (ROD / IOC / FOK). Market, IOC and FOK exist only in the continuous session. |
| Settlement | T+2 (普通交割). |
| Scope | The TWSE centralised auction market for equities, ETFs, ETNs, REITs and warrants. Bonds, block trades (鉅額交易), auctions/tenders and the Taipei Exchange (TPEx) are out of scope. |

## Tick Size (升降單位) — Operating Rules Article 62

TWSE publishes one table with a column per instrument type. Three columns
matter for the instruments in scope, and they disagree above NT$10:

| Price band (NT$) | Equity products | ETF / ETN / REIT | Warrants |
|---|---|---|---|
| 0.01 ≤ P < 5 | 0.01 | 0.01 | 0.01 |
| 5 ≤ P < 10 | 0.01 | 0.01 | 0.05 |
| 10 ≤ P < 50 | 0.05 | 0.01 | 0.10 |
| 50 ≤ P < 100 | 0.10 | 0.05 | 0.50 |
| 100 ≤ P < 150 | 0.50 | 0.05 | 1.00 |
| 150 ≤ P < 500 | 0.50 | 0.05 | 1.00 |
| 500 ≤ P < 1,000 | 1.00 | 0.05 | 5.00 |
| P ≥ 1,000 | 5.00 | 0.05 | 5.00 |

- **"Equity products"** is TWSE's own column heading (*Equity Product): common
  stock, foreign stock — the table's note confirms this covers both first and
  second listings — TDRs, closed-end securities investment trust fund
  beneficiary certificates, rights certificates, payment certificates and
  preferred shares with call warrants.
- The **ETF/ETN/REIT** column covers domestic-component, foreign-component,
  futures, leveraged/inverse, offshore and active ETFs, REIT beneficiary
  securities, and the corresponding ETN types. It is the two-tier
  `0.01 / 0.05` table that is so often mislabelled "the TWSE tick size".
- Convertible bonds and corporate bonds with warrants tick 0.05 below NT$150
  and 1.00 up to NT$1,000; corporate bonds are a flat 0.05; central registered
  government bonds a flat 0.01. Out of scope here.
- **Block trades quote on a flat NT$0.01 tick**, inside the day's ordinary
  price-limit range.

Bands are lower-inclusive and upper-exclusive, so a price sitting exactly on a
boundary takes the **coarser** tick of the band above. NT$49.95 is a legal
equity price; NT$50.05 is not.

## Daily Price Limit (升降幅度) — Operating Rules Article 63

| Security type | Limit |
|---|---|
| Stocks, foreign-stock first listings, closed-end fund beneficiary certificates, REIT beneficiary securities, TDRs, domestic-component ETFs, futures ETFs, rights/payment certificates, convertible bonds, domestic-component ETNs | ±10% of the day's auction reference price |
| Leveraged/inverse ETFs and ETNs whose underlying index components are all domestic securities | 10% × the fund's multiple |
| Corporate bonds | ±5% |
| Government bonds, foreign bonds, foreign-stock **secondary** listings, **newly listed common stocks in their first five sessions**, foreign-component ETFs, offshore ETFs, leveraged/inverse ETFs or ETNs with one or more foreign component securities, foreign-component ETNs | **No price limit** |
| Warrants, preferred shares with warrants, corporate bonds with warrants, company warrants | Derived from the underlying's own limit prices and the exercise ratio; index warrants from the underlying index's prior close × monetary value per point × exercise ratio × 10%. Warrants on foreign-component ETFs, offshore ETFs, foreign securities or foreign indices have no limit. |

**The percentage is not the limit price.** Article 62 still applies, so TWSE
moves the computed bound onto the tick grid *toward* the reference price. Its
published worked example, reproduced verbatim in the unit tests:

> Reference price 40.60. Limit-up = 40.60 × 110% = 44.66; limit-down =
> 40.60 × 90% = 36.54. The NT$10–50 band ticks at NT$0.05, so the candidate
> limit-up prices are 44.65 and 44.70 and the candidate limit-down prices are
> 36.50 and 36.55. Choosing 44.70 and 36.50 would exceed the 10% limit.
> The day's limit-up is therefore **44.65** and the limit-down **36.55**.

Two edge rules attach: if the converted amount is **less than NT$0.01 it counts
as NT$0.01** (NT$0.05 for bonds), and **no price may fall below NT$0.01**
(NT$0.05 for bonds).

## Auction Reference Price (開盤競價基準)

The anchor for both the price band and the 平盤以下 short-sale test. TWSE
determines it in this order:

1. The previous session's **closing price**.
2. If there was no closing price: the previous session's closing **highest bid**
   if it was above the previous auction reference price; otherwise the closing
   **lowest ask** if it was below it; otherwise the previous auction reference
   price unchanged.
3. On first listing, on ex-rights/ex-dividend price adjustments, and on
   resumption after a suspension: the adjusted reference determined under
   Articles 59, 59-1, 67 and 67-1.

A pipeline that hard-codes "previous close" is correct on ordinary days and
silently wrong on every day in cases 2 and 3.

## Trading Units and Odd Lots

| Security type | Trading unit |
|---|---|
| Stocks, foreign-stock first listings, rights/payment certificates, bond-conversion rights certificates, preferred shares with call warrants | 1,000 shares |
| Warrants, closed-end fund beneficiary certificates, REIT beneficiary securities, TDRs, all ETF types, company warrants, all ETN types | 1,000 units |
| Foreign-stock **secondary** listings, offshore ETFs | Not restricted to 1,000 |
| Convertible bonds, central registered government bonds, corporate bonds, corporate bonds with warrants | NT$100,000 par |

**Intraday odd lot (盤中零股交易)**, live since 26 October 2020:

- Orders 09:00–13:30; first call auction at 09:10, then every **5 seconds**
  (shortened again on 2 December 2024).
- 1–999 shares. **Limit ROD only** — quantity may be reduced or the order
  cancelled, but the price may **not** be amended.
- Same tick schedule and same price limit as regular trading. Odd-lot prints
  set no open, close, high or low.
- **Cash only: 不得使用信用交易及借券賣出.** Margin trading and SBL short sales
  are barred, so an odd-lot short sale does not exist.
- **Warrants and ETNs may not trade odd lot** (認購(售)權證及ETN不得進行零股交易).
- Unfilled orders are cleared at 13:30 and do not carry into the after-hours
  odd-lot session.

**After-hours odd lot (盤後零股交易)**: orders 13:40–14:30, one call auction at
14:30, otherwise the same constraints.

## Order Types and Session Availability

Six combinations exist: limit/market × ROD/IOC/FOK. TWSE's duration codes are
**ROD** (Rest of Day), **IOC** and **FOK**; there is no "ROH".

| Session | Accepted |
|---|---|
| Opening call auction (order entry from 08:30, match 09:00) | Limit ROD only |
| Continuous trading 09:00–13:25 | All six |
| Closing call auction 13:25–13:30 | Limit ROD only — market, IOC and FOK are returned (退單) |
| Intraday / after-hours odd lot | Limit ROD only |
| While intraday price stabilisation is triggered | Call auction; the same restriction applies |

Market orders are additionally **prohibited** for:

1. Securities with no daily price limit, including newly listed common stocks
   during their first five sessions — with no band, a market order can print
   anywhere.
2. Securities under an extended matching interval (延長撮合間隔時間), which are
   matched by call auction.
3. Margin or SBL **short sales** of securities restricted from pricing below
   the reference price — precisely so the print cannot land below 平盤. The
   same reasoning bars amending such an order's price below the reference.

## Short Selling

TWSE has **no locate concept**. A short sale is a distinct order-ticket type
(委託書種類), one of 現股 / 融資 / 融券 / 借券, arranged with the securities
firm or through the TWSE securities borrowing and lending system before the
order is entered. Naked shorting is structurally impossible rather than merely
prohibited, and the borrow is not something a pre-trade validator confirms.

The rule that rejects orders intraday is the **平盤以下 restriction**: a margin
or SBL short may not be priced *strictly below* the day's reference price.
Pricing exactly at the reference is permitted.

Since **23 September 2013** TWSE has published a daily inclusion list —
**平盤下得融(借)券賣出之證券名單**, report TWT92U — of the securities exempt
from that restriction. Its own notes define the list:

- It contains every listed security **eligible for margin trading (融資融券)**,
  excluding those that have not obtained eligibility and those suspended under
  Articles 4 and 5 of 有價證券得為融資融券標準.
- Per-security flags mark **暫停融券賣出** (margin short suspended), **暫停借券
  賣出** (SBL short suspended — typically the short balance has reached its
  cap), and **前一交易日收盤價跌停本日禁止平盤下融券、借券賣出**: where the
  previous session **closed limit-down** — or, with no close, the closing
  lowest ask was at the limit-down price — below-reference shorting is barred
  for that security today.
- Securities newly placed under altered trading methods intraday lose margin
  eligibility, and with it below-reference shorting, the same day.

The list therefore changes daily and per security. It is reference data to be
fetched, not a market-wide repeal to be assumed. Aggregate controls sit on top
of it (per-security SBL short balance and combined SBL-plus-margin-short caps
against outstanding shares, and a daily short order cap against recent average
volume); those are position-level constraints outside this validator.

## Foreign Investor Registration

Foreign investors must register with TWSE and obtain an **Investor ID** before
opening a trading account at a local securities firm. TWSE classifies them into
offshore/onshore × institutional/individual; the market calls an offshore
institution's identifier a **FINI ID**. Offshore investors appoint a domestic
agent (custodian) bank, which handles registration; since 24 February 2025 an
offshore foreign institutional investor may designate two or more custodians.
The review regime moved from permit to registration, and investment quotas for
offshore institutional investors were removed in July 2003.

TWSE publishes no check-digit or format specification for the Investor ID, so
the reference implementation validates its **presence only** and never
defaults it.

## Order Entry Reality

There is no public TWSE order-entry API. The TWSE OpenAPI service
(`openapi.twse.com.tw`) exposes 143 unauthenticated open-data endpoints —
market statistics, corporate disclosures, index and reference data — and
declares no security scheme and no order, trade or execution endpoints.

Orders reach the matching engine through a TWSE member securities firm. TWSE's
rules describe the automated order-entry report a firm submits, and its fields
map directly onto the validator's payload: 證券商代號, 委託書編號, **委託書種類
(融資、融券、借券)**, 委託人帳號, 有價證券代號, **交易種類 (普通、鉅額、零股)**,
單價, 數量, 買賣別. Automated orders may be entered from 30 minutes before the
session opens.

## Sources

- TWSE 集中市場交易制度介紹 — tick table, price limits and the worked example,
  trading units, sessions, order types, odd-lot rules, block trading:
  <https://www.twse.com.tw/zh/products/system/trading.html>
- TWSE Trading Mechanism Introduction (English edition of the same page):
  <https://www.twse.com.tw/en/products/system/trading.html>
- TWSE 平盤下得融(借)券賣出之證券名單 (report TWT92U) and its notes:
  <https://www.twse.com.tw/zh/trading/margin/twt92u.html>
- TWSE securities borrowing and lending Q&A:
  <https://www.twse.com.tw/zh/products/sbl/qa.html>
- TWSE foreign investor overview (registration, Investor ID, categories):
  <https://www.twse.com.tw/en/products/education/foreign/overview.html>
- TWSE OpenAPI specification (endpoint inventory, absence of order entry):
  <https://openapi.twse.com.tw/v1/swagger.json>
