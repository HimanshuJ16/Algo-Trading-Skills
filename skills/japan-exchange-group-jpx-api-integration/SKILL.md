---
name: japan-exchange-group-jpx-api-integration
description: >-
  Use when routing cash-equity orders to the Tokyo Stock Exchange arrowhead4.0 platform,
  enforcing four-character securities codes, the three published TSE tick size tables
  and absolute-yen daily price limits.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: jpx, japan-exchange, tse, arrowhead, tokyo-stock-exchange, board-lot, tick-sizes
  brokers_frameworks: "TSE arrowhead4.0 Cash Equity Trading System; JPX Securities Identification Code Committee (SICC) codes; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building market gateways, order routers, or pre-trade risk filters for Japan Exchange Group cash equities (Tokyo Stock Exchange, arrowhead4.0 since 5 November 2024). Order entry to arrowhead requires strict compliance with four TSE microstructure rules: four-character securities codes (numeric for issues coded before January 2024, **alphanumeric** for issues coded since), the price-tier **tick size** (呼値の単位) drawn from one of three published tables, the **trading unit** (売買単位, 100 shares for domestic stocks), and the **daily price limit** (制限値幅), which TSE sets in **absolute yen**, not as a percentage.

## When NOT to Use

- **As a substitute for exchange-side controls**: this is a client-side pre-trade filter. arrowhead is authoritative and can reject an order this engine approves. Trading halts, suspensions, special quotes (特別気配) and their renewal price ranges, the pre-opening and closing auction phases, short-sale price restrictions, and ToSTNeT off-auction trading are **not** modelled.
- **Derivatives**: OSE/TOCOM futures and options trade on J-GATE with their own contract specifications, price limits and circuit breakers. Nothing here applies to them.
- **Deriving the applicable tick table from the price**: the tick *table* (TOPIX500 / single-unit ETF / other) is a property of the **issue** and is announced per-issue by TSE. Only the *band within* a table is chosen by price. Passing the wrong table produces confidently wrong validation.
- **Inferring a broadened price limit**: TSE broadens daily price limits on the third business day after two consecutive qualifying sessions and publishes the affected issues. The engine cannot derive this; pass the published figure via `daily_price_limit_override_jpy`.

## Prerequisites

- A TSE order request (`local_code`, `side`: `BUY`/`SELL`, `price_jpy`, `quantity` in shares, `reference_price_jpy`).
- The **base price** (基準値段) for the issue — normally the previous day's closing price, or the last special quote. It anchors the daily price limit band. It does **not** select the tick size.
- The issue's **tick table** (`TOPIX500`, `ETF_SINGLE_UNIT`, `OTHER`), from TSE's published applicability list — not from an index constituent snapshot.
- The issue's **trading unit**: 100 shares for domestic stocks; 1 or 10 for ETFs, ETNs, REITs and leveraged products.
- Current JPX schedules — see `references/standards.md` for the dated tables in force.

## Workflow

1. **Securities Code Validation**:
   - Normalise to four uppercase characters. Positions 1 and 3 are always digits; positions 2 and/or 4 may be a digit or one of the 19 uppercase letters SICC uses (`ACDFGHJKLMNPRSTUWXY` — `B`, `E`, `I`, `O`, `Q`, `V`, `Z` are excluded).
   - Do **not** apply an `isdigit()` test: every issue listed since 1 January 2024 (`130A`, `9A76`) would be rejected pre-trade.
2. **Tick Table Selection — a property of the issue, not the price**:
   - `TOPIX500` for TOPIX500 constituents (TOPIX100 + TOPIX Mid400), and for ETFs/ETNs/leveraged products with a trading unit of 10 or above.
   - `ETF_SINGLE_UNIT` for ETFs/ETNs/leveraged products with a trading unit of one.
   - `OTHER` for all remaining domestic stocks.
3. **Tick Size Audit (呼値の単位)** — bands have **inclusive** upper bounds (「以下」), so a price sitting exactly on a boundary takes the **finer** tick of the lower band:
   - `TOPIX500`: $P \le 1{,}000 \implies \Delta P = 0.1$; $P \le 3{,}000 \implies 0.5$; $P \le 10{,}000 \implies 1$; $P \le 30{,}000 \implies 5$; then 10 / 50 / 100 / 500 / 1,000 / 5,000 / 10,000.
   - `OTHER`: $P \le 3{,}000 \implies \Delta P = 1$; $P \le 5{,}000 \implies 5$; $P \le 30{,}000 \implies 10$; $P \le 50{,}000 \implies 50$; then 100 / 500 / 1,000 / 5,000 / 10,000 / 50,000 / 100,000.
   - The minimum tick is **JPY 0.1**, so TSE prices are not whole yen. Test tick alignment in exact decimal arithmetic — a binary float tolerance on a JPY 0.1 or JPY 0.5 increment can accept a price arrowhead will reject.
4. **Trading Unit Audit (売買単位)**:
   - Verify `quantity` is a strictly positive multiple of the issue's trading unit — 100 shares for domestic stocks since 1 October 2018, but 1 or 10 for ETFs/ETNs/REITs.
5. **Daily Price Limit Audit (制限値幅)** — **absolute yen**, keyed to the base price, with **exclusive** band bounds (「未満」), the opposite convention to the tick table:
   - $P_{base} < 100 \implies \pm 30$; $< 200 \implies \pm 50$; $< 500 \implies \pm 80$; $< 1{,}000 \implies \pm 150$; $< 3{,}000 \implies \pm 500$; $< 10{,}000 \implies \pm 1{,}500$; and so on up to $\pm 10{,}000{,}000$ at or above JPY 50 million.
   - The band bounds are **inclusive**: an order at exactly the limit is the stop-high/stop-low price and is tradeable.
   - Validate the base price is finite and strictly positive **before** using it — a zero or missing previous close cannot anchor a band.
6. **Audit Report Generation**: Output structured `JpxOrderReport` carrying the applied tick table, tick size, unit count, limit amount and both band bounds, so a rejection can be repriced rather than merely reported.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Modelling the daily price limit as a percentage**: TSE sets it in absolute yen. The implied percentage ranges from roughly 33% at the bottom of the table to roughly 10% higher up, and it is *not* monotonic. A flat ±20% rule both falsely rejects legitimate orders (base JPY 150: TSE allows JPY 100–200, ±20% allows only JPY 120–180) and waves through orders arrowhead will reject (base JPY 9,000: TSE allows JPY 7,500–10,500, so a +18.9% order at JPY 10,700 passes a ±20% check and fails at the exchange).
- **Rejecting alphanumeric securities codes**: SICC has assigned codes containing letters since 1 January 2024 because the numeric range 1300–9999 is running out. A four-digit validator silently blocks every recent listing. Codes issued before then are unchanged and remain numeric.
- **Using one tick table for all issues**: a TOPIX500 constituent at JPY 2,500 ticks at **JPY 0.5**; a non-TOPIX500 issue at the same price ticks at **JPY 1**. Validating Toyota (7203) against the `OTHER` table rejects the perfectly valid price JPY 2,500.5; validating a non-TOPIX500 issue against the `TOPIX500` table sends arrowhead a price it will reject.
- **Inferring tick-table membership from an index constituent list**: TSE announces the TOPIX500 tick table's application per issue with its own effective date ("Handling of Tick Sizes from *date* onward"), under Rule 14, Paragraph 3, Item 1-b of the Business Regulations. An issue's index membership and its tick table can diverge for several sessions.
- **Getting band inclusivity backwards**: tick bands are 以下 (inclusive upper bound) and price-limit bands are 未満 (exclusive upper bound). A price of exactly JPY 5,000 takes the finer JPY 5 tick on the `OTHER` table, while a base price of exactly JPY 100 falls into the ±JPY 50 band, not ±JPY 30.
- **Float tick arithmetic**: JPY 0.1 has no exact binary representation. `abs(price / tick - round(price / tick)) < 1e-5` accepts prices that are not whole ticks once the price is large. Compare in `Decimal`.
- **Assuming a JPY 1 price floor**: TSE's tick table quotes TOPIX500 constituents at or below JPY 1,000 in JPY 0.1 increments, so sub-yen prices are expressible. Do not hard-code a JPY 1 minimum.
- **Treating every instrument as a 100-share lot**: an ETF with a trading unit of one is rejected as an odd lot by a hard-coded `% 100` test.
- **Ignoring broadened limits**: after two consecutive limit-locked sessions with no volume, TSE broadens the limit for that issue. An engine holding the standard schedule will falsely reject orders that are legitimately inside the broadened band.

## Verification

- Instantiate `JpxStockExchangeApiEngine`. Route a Toyota order (`local_code="7203"`, `tick_table="TOPIX500"`, Price $=\text{JPY } 2{,}500.5$, Qty $=500$ shares / 5 units, base price $=\text{JPY } 2{,}500$). Verify the engine selects $\Delta P = \text{JPY } 0.5$, computes the band $\text{JPY } 2{,}000 - \text{JPY } 3{,}000$ (limit $\pm\text{JPY }500$), and returns `JPX_ORDER_VALIDATED`.
- Confirm table sensitivity: the same JPY 2,500.5 price against `tick_table="OTHER"` must return `INVALID_TICK_SIZE`.
- Confirm the absolute-yen band: against a JPY 9,000 base price, JPY 10,700 (+18.9%) must return `PRICE_LIMIT_EXCEEDED`; against a JPY 150 base price, JPY 195 (+30%) must be **accepted**.
- Confirm code handling: `130A` must validate, `130B` must raise (excluded letter), and `A130` must raise (letter in position 1).
- Run the test suite:
```bash
python -m unittest discover -s skills/japan-exchange-group-jpx-api-integration/scripts
```

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `korea-exchange-krx-api-integration`
- `japan-fsa-high-speed-trading-registration`
