# Pre-Flight Checklist — KRX (KOSPI / KOSDAQ) Order Gateway

## Instrument

- [ ] Is the security class (`STOCK` / `ETF_ETN`) taken from reference data, not inferred from the price or the code?
- [ ] Is the short code six characters, five digits then a digit **or** a letter (`I`, `O`, `U` excluded)?
- [ ] Does the validator accept `03473K` and `18064K` — i.e. is it free of an `isdigit()` test?
- [ ] Are leading zeros preserved end to end, with zero-padding opt-in rather than automatic?

## Tick Size (호가가격단위)

- [ ] Is the schedule the one in force since **25 January 2023** (1 / 5 / 10 / 50 / 100 / 500 / 1,000 KRW at the 2,000 / 5,000 / 20,000 / 50,000 / 200,000 / 500,000 boundaries)?
- [ ] Are band bounds treated as **exclusive** (「미만」), so KRW 2,000 takes the KRW 5 tick?
- [ ] Do ETFs and ETNs use the flat **KRW 5** tick rather than the stock schedule?
- [ ] Is the tick selected from the **order** price, and alignment tested in exact decimal arithmetic rather than a float tolerance?
- [ ] For any replay before 25 January 2023, are the **old per-board** tables loaded instead?

## Daily Price Limit (가격제한폭)

- [ ] Is the base price validated as finite and strictly positive **before** it is used?
- [ ] Is the limit computed as an **amount** — base × pct, truncated to the **base price's** tick — rather than as an `abs(P − base) / base` deviation test?
- [ ] Does the implementation reproduce the KRX worked example: base 9,940 → amount 2,980 → band 6,960 – 12,920?
- [ ] Are both bounds treated as **inclusive**, so an order at exactly 상한가 is accepted?
- [ ] Is the percentage right for the board — 30% KOSPI/KOSDAQ, 15% KONEX?
- [ ] Are exempt instruments (정리매매, 신주인수권증권·증서, ELW) flagged so the band is skipped while the tick check still runs?

## Order Payload

- [ ] Is `side` validated against `BUY`/`SELL` rather than merely upper-cased?
- [ ] Is `quantity` a strictly positive integer number of shares?
- [ ] Are NaN and infinite prices rejected rather than silently failing every comparison?

## Report & Error Handling

- [ ] Does the report carry the tick size, the limit amount and **both** band bounds, so a rejection can be repriced?
- [ ] Is malformed input **raised**, never returned as a status a caller could mistake for an exchange rejection?
- [ ] Does the code avoid branching on statuses the engine never emits?

## Scope

- [ ] Is it understood that VI triggers, circuit breakers, halts, auctions and short-sale rules are **not** modelled here, and that the KRX matching engine remains authoritative?
