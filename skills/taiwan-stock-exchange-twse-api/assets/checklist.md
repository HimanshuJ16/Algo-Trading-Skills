# Pre-Flight Checklist — TWSE Order Gateway

## Instrument & Reference Data

- [ ] Is the security class (`EQUITY` / `ETF_REIT` / `ETN` / `WARRANT`) taken from reference data, not inferred from the code or the price?
- [ ] Is the **auction reference price** (開盤競價基準) the value TWSE published for the day, rather than a previous close carried forward?
- [ ] Are ex-rights/ex-dividend days, first listings, resumptions from suspension and no-trade sessions known to move the reference price off the previous close?
- [ ] Is the price-limit status correct — standard 10%, `10 × multiple` for a domestic leveraged/inverse fund, or exempt?
- [ ] Is the trading unit 1,000, or the security's actual unit for a foreign-stock secondary listing or offshore ETF?

## Tick Size (升降單位, Art. 62)

- [ ] Do equities use the **six-band** schedule — 0.01 / 0.05 / 0.10 / 0.50 / 1.00 / 5.00 at the 10 / 50 / 100 / 500 / 1,000 boundaries?
- [ ] Is the two-tier `0.01 below 50 / 0.05 at or above` table applied **only** to ETFs, ETNs and REITs?
- [ ] Do warrants use their own schedule, which breaks at NT$5?
- [ ] Are band bounds treated as **exclusive** (「未滿」), so NT$50.00 takes the NT$0.10 tick and NT$50.05 is rejected?
- [ ] Is the tick selected from the **order** price's band, and alignment tested in exact decimal arithmetic rather than a float tolerance?

## Daily Price Limit (升降幅度, Art. 63)

- [ ] Is the reference price validated as finite and strictly positive **before** it is used?
- [ ] Are the computed bounds **snapped onto the tick grid toward the reference**, rather than left as raw percentages?
- [ ] Does the implementation reproduce TWSE's worked example: reference 40.60 → limit-up **44.65**, limit-down **36.55**?
- [ ] Are both bounds treated as **inclusive**, so an order exactly at the limit-up price is accepted?
- [ ] Is an amount under NT$0.01 raised to NT$0.01, and is NT$0.01 enforced as the price floor?
- [ ] Are exempt securities flagged so the band is skipped while the **tick check still runs**?

## Sessions, Order Types & Odd Lots

- [ ] Are market, IOC and FOK orders confined to the continuous session (09:00–13:25)?
- [ ] Do the opening and closing call auctions accept **limit-ROD only**?
- [ ] Is the duration code `ROD` — not the non-existent "ROH"?
- [ ] Are market orders blocked for securities with **no price limit**, including a new common stock's first five sessions?
- [ ] Do odd-lot orders enforce 1–999 shares, limit-ROD only, and no price amendment?
- [ ] Are warrants and ETNs blocked from both odd-lot sessions?
- [ ] Is odd-lot trading enforced as **cash only** — no margin, no SBL?

## Short Selling

- [ ] Is the ticket type (現股 / 融資 / 融券 / 借券) carried as a field distinct from buy/sell, rather than collapsed into a `SHORT_SELL` side?
- [ ] Is the **平盤以下** rule implemented as *strictly below* the reference price, so a short exactly at 平盤 is accepted?
- [ ] Is today's 平盤下得融(借)券賣出 list fetched fresh, including the 暫停融券賣出 / 暫停借券賣出 / previous-close-limit-down flags?
- [ ] Are market orders blocked for short sales of restricted securities?
- [ ] Is an odd-lot short sale rejected outright rather than checked for a borrow?
- [ ] Is it understood that TWSE has **no locate** — the borrow is arranged before entry, and a "locate available" boolean models nothing?

## Identity & Payload

- [ ] Is a TWSE Investor ID (FINI ID for an offshore institution) required on every order?
- [ ] Does the engine ship **no** default Investor ID, fabricated or otherwise?
- [ ] Is `quantity` a strictly positive integer number of shares?
- [ ] Are NaN and infinite prices rejected rather than silently failing every comparison?
- [ ] Is a price supplied on a market order treated as a caller bug?

## Report & Error Handling

- [ ] Does the report carry the applied tick, both band bounds and the nearest legal prices, so a rejection can be repriced?
- [ ] Is malformed input **raised**, never returned as a status a caller could mistake for an exchange rejection?

## Scope

- [ ] Is it understood that intraday price stabilisation, delayed open/close, disposition and altered-trading-method securities, aggregate short-sale caps, bonds, block trades and TPEx are **not** modelled — and that the TWSE matching engine remains authoritative?
- [ ] Is it understood that TWSE offers **no public order-entry API**, and that orders route through a member securities firm?
