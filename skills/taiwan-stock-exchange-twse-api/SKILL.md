---
name: taiwan-stock-exchange-twse-api
description: >-
  Use when an order is about to be sent to the Taiwan Stock Exchange and must first be legal for the security it names — the six-band equity tick schedule of Operating Rules Article 62 (which is not the two-tier ETF table), the Article 63 ±10% band whose bounds are snapped onto the tick grid toward the auction reference price, the 1,000-share trading unit and its odd-lot exceptions, the session-by-session order-type matrix, and the 平盤以下 short-sale restriction that replaces any notion of a US-style locate.
domain: Global Market Integration & FX
subdomain: Taiwanese Market Connectivity & TWSE Gateway
tags: ["twse", "taiwan-stock-exchange", "fini", "tick-size", "price-limit", "odd-lot-trading", "short-selling"]
brokers_frameworks: ["TWSE centralised market (集中交易市場) matching engine", "TWSE Operating Rules Articles 62 and 63", "TWSE 平盤下得融(借)券賣出 daily list (report TWT92U)", "TWSE OpenAPI (open data only, no order entry)", "TWSE member securities-firm order-entry link (電腦自動交易買賣申報)", "Python Decimal"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a TWSE order is constructed programmatically and its price,
quantity, order type and short-sale status have to be legal for the specific
security *before* the order reaches a securities firm. TWSE enforces four
microstructure rules that each look simpler than they are: a **tick schedule**
(升降單位) that differs by instrument class and whose bands are upper-exclusive,
a **daily price limit** (升降幅度) that is a percentage *and* a grid-snapping
rule, a **trading unit** with real exceptions, and a **short-sale price
restriction** (平盤以下) that has no US analogue.

Use it as well when reviewing existing TWSE code: the two-tier
`0.01 below NT$50 / 0.05 at or above` table is widely copied as "the TWSE tick
size" and is in fact the ETF/ETN/REIT schedule. Applied to a stock it accepts
prices the matching engine rejects at every price above NT$10.

## When NOT to Use

- **As a substitute for exchange-side controls.** This is a client-side
  pre-trade filter. The TWSE matching engine is authoritative and can reject an
  order this engine approves.
- **As an order-entry client.** There is no public TWSE order-entry API. The
  TWSE OpenAPI service publishes open market and corporate data over 140
  unauthenticated endpoints and carries no order, trade or execution
  endpoints. Orders reach the matching engine only through a TWSE member
  securities firm's link.
- **For the price-stabilisation and disposition machinery.** The intraday
  price stabilisation measure (瞬間價格穩定措施, ±3.5% against the rolling
  reference with a 2-minute delayed match), the delayed open/close (暫緩開盤
  09:02 / 暫緩收盤 13:33), disposition securities (處置證券) with their
  extended matching intervals and prefunding, and altered-trading-method
  securities (變更交易方法) are **not** modelled. They do not change the tick
  or the band; they change whether and when the order trades at all.
- **For bonds, block trades and auctions.** Convertible/corporate/government
  bonds trade in NT$100,000 par units with their own 5%-or-no-limit bands;
  block trades (鉅額交易, minimum 500 trading units or NT$15m) quote on a flat
  NT$0.01 tick. All are out of scope.
- **For TPEx.** The Taipei Exchange and its Emerging Stock Board are a separate
  market with separate rules. Nothing here is transferable without checking.
- **For inferring the security class from the code or the price.** The class
  selects the tick schedule, the odd-lot eligibility and the price-limit
  multiple. It is reference data. Validating an ETF on the `EQUITY` default
  rejects legal prices; validating a stock on `ETF_REIT` accepts illegal ones.

## Prerequisites

- Python 3.9+. No third-party dependencies — `decimal` from the standard
  library carries every price comparison.
- A TWSE **Investor ID**, issued on registration under the Regulations
  Governing Investment in Securities by Overseas Chinese and Foreign Nationals.
  An offshore institution's is what the market calls a FINI ID; offshore
  investors register through a domestic agent (custodian) bank.
- The day's **auction reference price** (開盤競價基準) for the security, as
  published by TWSE — see the Workflow for why the previous close is not a
  safe substitute.
- The security's **class** (`EQUITY`, `ETF_REIT`, `ETN`, `WARRANT`), its
  **price-limit status** (standard 10%, a leveraged/inverse multiple, or
  exempt), and its presence on that day's **平盤下得融(借)券賣出** list if the
  order is a short sale. All three are reference data, not inferences.

## Workflow

1. **Resolve the reference data first.** Security class, trading unit,
   price-limit percentage or exemption, and short-sale eligibility all come
   from TWSE publications for *that trading day*. Passing an order to the
   validator with a guessed class is worse than not validating it.
2. **Investor ID.** Reject an order that carries none. Never supply a default:
   an order stamped with a fabricated registration identifier is worse than one
   rejected for lacking a real one.
3. **Ticket type (委託書種類) against side.** TWSE's order entry carries the
   buy/sell flag and the ticket type as separate fields — 現股 / 融資 / 融券 /
   借券. A short sale is `SELL` on a `MARGIN_SHORT` or `SBL_SHORT` ticket;
   `MARGIN_LONG` is a financed purchase and must be `BUY`.
4. **Session constraints, before any price arithmetic.**
   - Odd-lot sessions (盤中零股 09:00–13:30, matching from 09:10 every 5
     seconds; 盤後零股 13:40–14:30, one auction at 14:30) take 1–999 shares,
     are **cash only** — 不得使用信用交易及借券賣出, so an odd-lot short is
     never valid — and exclude warrants and ETNs entirely.
   - Regular sessions take a positive multiple of the trading unit, 1,000
     shares by default. Secondary listings of foreign stocks and offshore ETFs
     are 不以1,000股為限: pass `trading_unit` explicitly rather than forcing
     them through the default.
   - Market, IOC and FOK exist **only** in the continuous session
     (09:00–13:25). The opening and closing call auctions accept limit-ROD
     alone and return (退單) anything else. TWSE's duration codes are ROD, IOC
     and FOK — there is no "ROH".
5. **Market-order carve-outs.** A market order is barred where there is no
   price limit (a new common stock's first five sessions, foreign-component and
   offshore ETFs, secondary-listed foreign stocks) and barred for a short sale
   of a security restricted below the reference price, precisely so the print
   cannot land below 平盤.
6. **Tick alignment (Article 62).** Take the tick from the **order price's**
   band under the **instrument's** schedule. Bands read 「10元至未滿50元」 —
   lower-inclusive, upper-**exclusive** — so a price exactly on a boundary
   takes the coarser tick above: NT$49.95 is legal, NT$50.05 is not. Compare
   with `Decimal` modulo, never a float tolerance.
7. **Daily price limit (Article 63, read with Article 62).** Compute the
   amount as reference × pct, then move the bound **toward** the reference to
   reach the grid, because the outward tick would breach the band. TWSE's own
   worked example: reference 40.60 → 44.66 and 36.54 → limit-up **44.65**,
   limit-down **36.55**. If the amount converts to less than NT$0.01 it counts
   as NT$0.01, and no price may fall below NT$0.01. Both bounds are inclusive.
8. **平盤以下 short-sale restriction.** A margin or SBL short may not be priced
   *strictly below* the auction reference price unless the security is on that
   day's 平盤下得融(借)券賣出 list. Pricing exactly *at* the reference is always
   allowed. The list is published daily and is not static: a security drops off
   it when margin trading is suspended, when the SBL short balance hits its
   cap, or when the previous session **closed limit-down**.
9. **Report, don't just refuse.** Return the applied tick, the band and the
   nearest legal prices so a rejected order can be repriced rather than
   discarded.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using the ETF tick table for stocks.** `0.01 below NT$50, 0.05 at or above`
  is the ETF/ETN/REIT schedule. The equity schedule has six bands —
  0.01 / 0.05 / 0.10 / 0.50 / 1.00 / 5.00 breaking at 10, 50, 100, 500 and
  1,000. The two agree only below NT$10. A validator carrying the ETF table
  passes TSMC (2330) at NT$1,102.50, where the real tick is NT$5, and passes
  NT$120.03, where it is NT$0.50. Warrants have a *third* schedule that breaks
  at NT$5.
- **Treating the price limit as `abs(P − ref)/ref <= 0.10`.** The percentage
  produces 44.66 from a 40.60 reference, which is off the NT$0.05 grid, and the
  nearest outward tick 44.70 is above the band. TWSE's answer is 44.65. The
  naive test both accepts an unquotable price and mis-states the limit-up
  price, which is exactly where orders cluster.
- **Anchoring on "yesterday's close".** The band is measured from the day's
  **auction reference price** (開盤競價基準). That equals the previous close
  only in the ordinary case. Where there was no close, TWSE substitutes the
  previous session's closing best bid (if above the old reference) or best ask
  (if below it); on first listing, on ex-rights/ex-dividend days and on
  resumption from suspension it is an adjusted reference set under Articles 59,
  59-1, 67 and 67-1. Carrying a close forward silently mis-bands every one of
  those days.
- **Modelling TWSE short selling as a "borrow locate".** TWSE has no locate
  concept. A short is a distinct order ticket — 融券 (margin short) or 借券賣出
  (SBL short) — arranged before entry, which is what makes naked shorting
  structurally impossible rather than merely prohibited. The rule that actually
  rejects orders intraday is 平盤以下: a boolean "locate available" flag models
  none of it.
- **Assuming the below-close short-sale exemption is permanent.** It is a daily
  list, not a market-wide repeal. A security leaves it when margin trading is
  suspended, when the SBL short balance reaches its cap, or when the previous
  session closed limit-down (or, absent a close, the closing best ask was at
  the limit-down price). Cache the list for a day and you will short into a
  restriction.
- **Letting an odd-lot short through.** Both odd-lot sessions are cash only.
  An `odd_lot=True, side="SHORT_SELL"` order is not a partially-valid order to
  be checked for a borrow — it is invalid on its face.
- **Sending a market, IOC or FOK order into a call auction.** They exist only
  in the continuous session; the opening and closing auctions return them.
  A strategy that switches to IOC near the close silently loses its orders at
  13:25.
- **Sending a market order to a security with no price limit.** TWSE refuses
  them there deliberately — with no band, a market order can print anywhere.
  New listings in their first five sessions are the common case.
- **Float tolerance on tick alignment.** `abs(price % tick) < 1e-4` is a
  tolerance in NT$ against a grid whose coarsest step is NT$5, and it is a
  binary-float test on a decimal lattice. `Decimal("550.03") % Decimal("0.05")`
  is exact; `550.03 % 0.05` is not.
- **Folding malformed input into a rejection status.** A NaN price makes every
  `<=` comparison return `False`, so a data-quality failure is reported as a
  *rule breach*. Bad enumerations, non-positive quantities, a missing reference
  price and a price on a market order all raise `ValueError` here; only
  exchange rules produce a report.

## Verification

- Reproduce TWSE's published example:
  `get_daily_price_limit_bounds("40.60")` must return
  `(Decimal("36.55"), Decimal("44.65"))` — not 36.54 / 44.66, and not
  36.50 / 44.70.
- Confirm class sensitivity at one price: NT$44.66 must be on-tick for
  `ETF_REIT` and off-tick for `EQUITY`; NT$1,102.50 must be off-tick for
  `EQUITY`.
- Confirm the boundary convention: NT$49.95 on-tick, NT$50.05 off-tick,
  NT$50.10 on-tick for an equity.
- Confirm the short-sale rule: an `SBL_SHORT` at the reference price is
  accepted, the same order one tick lower returns
  `SHORT_SALE_BELOW_REFERENCE_RESTRICTED`, and it is accepted again with
  `below_reference_short_sale_permitted=True`.
- Confirm session gating: `MARKET`/`IOC`/`FOK` in either call auction returns
  `ORDER_TYPE_NOT_AVAILABLE_IN_SESSION`; an odd-lot order on a margin or SBL
  ticket returns `CREDIT_TICKET_NOT_PERMITTED_ODD_LOT`.
- Confirm the engine ships **no** default Investor ID:
  `TaiwanStockExchangeTwseEngine().investor_id is None`, and an order without
  one returns `MISSING_INVESTOR_ID`.
- Confirm the input guards: a NaN price, a zero reference price,
  `side="SHORT_SELL"`, `time_in_force="ROH"`, `quantity=0` and a price on a
  `MARKET` order must each raise `ValueError` rather than return a report.
- Run the test suite:
```bash
cd skills/taiwan-stock-exchange-twse-api/scripts
python -m unittest discover -s skills/taiwan-stock-exchange-twse-api/scripts
```

## Related Skills

- `korea-exchange-krx-api-integration`
- `hong-kong-exchange-hkex-orion-api`
- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `short-selling-borrow-cost-and-availability-modeling`
- `shanghai-shenzhen-connect-programs`
