---
name: korea-exchange-krx-api-integration
description: >-
  Use when routing cash-equity orders to Korea Exchange KOSPI or KOSDAQ on EXTURE 3.0,
  enforcing six-character short codes that may end in a letter, the revised tick size
  schedule and the truncated daily price limit band.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: krx, korea-exchange, kospi, kosdaq, exture-3, krw-tick-sizes, price-limit
  brokers_frameworks: "KRX EXTURE 3.0 matching engine; KRX short codes (단축코드); Koscom Gateway; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building market gateways, order routers, or pre-trade risk filters for Korea Exchange cash equities (KOSPI, KOSDAQ; KONEX with a percentage override). Order entry to KRX requires compliance with three microstructure rules that are each easy to get subtly wrong: the six-character **short code** (단축코드), whose sixth character may be a letter; the **tick size** (호가가격단위), which KRX revised on **25 January 2023** and which is flat at KRW 5 for ETFs and ETNs; and the **daily price limit** (가격제한폭), which KRX computes as a *truncated amount* added to and subtracted from the base price, not as a percentage deviation test.

## When NOT to Use

- **As a substitute for exchange-side controls**: this is a client-side pre-trade filter. The KRX matching engine is authoritative and can reject an order this engine approves.
- **For the price-stabilisation machinery**: Volatility Interruption (변동성완화장치) static and dynamic triggers, market-wide circuit breakers and sidecars, trading halts and suspensions are **not** modelled. They do not change the tick or the band; they change whether the order trades at all.
- **For auction and off-hours sessions**: the opening and closing call auctions, the off-hours single-price sessions and their own price ranges, and the listing-day base-price auction are out of scope.
- **For derivatives and ELWs**: KOSPI200 futures and options trade with their own contract specifications and price limits. ELWs carry **no** daily price limit and trade in units of 10 warrants; only their KRW 5 tick matches the `ETF_ETN` schedule here.
- **For inferring the security class from the code**: whether an issue is a stock or an ETF/ETN determines both the tick schedule and the code pattern. It is a property of the instrument. Passing an ETF on the `STOCK` default validates it against ticks that are far too coarse.

## Prerequisites

- A KRX order request (`local_code`, `side`: `BUY`/`SELL`, `price_krw`, `quantity` in shares, `reference_price_krw`).
- The **base price** (기준가격) for the issue — normally the previous session's closing price, adjusted for corporate actions. It anchors the daily price limit band and supplies the tick used to truncate the limit amount. It does **not** select the tick for the order price.
- The issue's **security class** — `STOCK` or `ETF_ETN`.
- Current KRX schedules — see `references/standards.md` for the dated tables in force.

## Workflow

1. **Short Code Validation (단축코드)**:
   - Normalise to six uppercase characters. The first five are digits; the sixth is a digit **or** a letter drawn from the 23-letter set that excludes `I`, `O` and `U`.
   - Do **not** apply an `isdigit()` test: it rejects listed, actively traded preferred lines (`00781K`, `03473K`, `18064K`, `02826K`) and every stock code KRX has issued since 1 January 2024.
   - Treat leading zeros as significant. Zero-padding a short input is opt-in (`allow_zero_pad=True`), because padding a mistyped code routes the order to a *different real instrument* rather than rejecting it.
2. **Tick Size Audit (호가가격단위)** — bands are 「이상 ~ 미만」, so the upper bound is **exclusive** and a price exactly on a boundary takes the **coarser** tick of the band above:
   - `STOCK`: $P < 2{,}000 \implies \Delta P = 1$; $P < 5{,}000 \implies 5$; $P < 20{,}000 \implies 10$; $P < 50{,}000 \implies 50$; $P < 200{,}000 \implies 100$; $P < 500{,}000 \implies 500$; else $1{,}000$.
   - `ETF_ETN`: $\Delta P = \text{KRW } 5$ at every price. ETFs, ETNs and ELWs were excluded from the 2023 revision.
   - Verify the order price is an exact multiple of $\Delta P$, selected from the **order** price's band.
3. **Daily Price Limit Audit (가격제한폭)** — a truncated amount, **not** a percentage deviation test:
   - $A = \operatorname{trunc}\!\left(P_{base} \times \tfrac{pct}{100},\ \Delta P(P_{base})\right)$ — the sub-tick remainder is discarded (절사) using the tick of the **base price's** band.
   - 상한가 $= P_{base} + A$; 하한가 $= P_{base} - A$. Both bounds are **inclusive**: an order at exactly 상한가 is the limit-up price and is tradeable.
   - $pct$ is 30 for KOSPI and KOSDAQ (since 15 June 2015) and 15 for KONEX. Issues in liquidation trading (정리매매) and subscription warrants/rights (신주인수권증권·증서) have **no** limit — set `price_limit_exempt=True`.
4. **Audit Report Generation**: Output a structured `KrxOrderReport` carrying the applied tick size, the limit amount and both band bounds, so a rejection can be **repriced** rather than merely reported.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Carrying the pre-2023 tick schedule.** KRX revised tick sizes on **25 January 2023**, the first change since 2010. Three bands narrowed: 1,000–2,000 (KRW 5 → 1), 10,000–20,000 (KRW 50 → 10) and 100,000–200,000 (KRW 500 → 100). A stale table rejects legitimate prices wholesale — Samsung Electronics at KRW 150,200 is a **valid** price on today's KRW 100 tick and was invalid on the old KRW 500 tick. Widely republished third-party tables, and some official English-language material, still show the old schedule.
- **Assuming KOSPI and KOSDAQ always shared one schedule.** Before 25 January 2023 they did not: KOSDAQ capped its tick at KRW 100 for prices at or above KRW 100,000, where KOSPI charged KRW 500 up to KRW 500,000 and KRW 1,000 above it. The same revision unified them. Any backtest reconstructing quotes before that date needs the per-board historical tables, not today's.
- **Applying the stock tick schedule to ETFs and ETNs.** They tick at a flat **KRW 5** at every price. An ETF at KRW 15,005 is perfectly legal; validated against the stock schedule's KRW 10 tick it is falsely rejected, and an ETF at KRW 15,010 validated against a KRW 5 expectation hides nothing but wastes the finer grid.
- **Modelling the price limit as `abs(P - base) / base <= 0.30`.** KRX truncates the limit *amount* to the base price's tick before applying it. Its own worked example: base KRW 9,940 → 9,940 × 0.3 = 2,982 → truncated to **2,980** → band KRW 6,960–12,920. The naive test accepts KRW 12,922 (+30.00%), which is both outside the band and off-tick. The gap is at most one tick, but on the limit-up print that is exactly where orders cluster.
- **Rejecting short codes containing letters.** `isdigit()` blocks `00781K`, `03473K`, `18064K` and `02826K` today. KRX announced in May 2023 that codes issued from 1 January 2024 mix letters into the sixth character of stock short codes (and into ETN/ELW codes more broadly), excluding `I`, `O` and `U`. Previously issued codes are never reissued, so both forms coexist permanently.
- **Silently zero-padding a short code.** `"5930".zfill(6)` is `"005930"` — Samsung Electronics. So is `"5".zfill(6)` → `"000005"`, a different listed instrument. Padding turns a typo that should have been rejected into an order on the wrong issue. Restore leading zeros at the boundary where you *know* they were stripped, not inside the validator.
- **Dividing by a base price you never checked.** A missing or zero previous close makes a percentage-deviation test raise `ZeroDivisionError` inside the routing path; a NaN base price makes every `<=` comparison return `False`, so the order is reported as a *rule breach* rather than as the data-quality failure it is. Validate that the base price is finite and strictly positive first.
- **Float tolerance on tick alignment.** `abs(price / tick - round(price / tick)) < 1e-5` is a tolerance in *tick units*: at the KRW 1,000 tick it silently accepts a price up to a hundredth of a won off the grid, and it is a binary-float test on a whole-won lattice. Compare with `Decimal` modulo.
- **Branching on a status the engine never emits.** Malformed input — bad code, unknown side, non-positive quantity or base price — is raised as `ValueError`, not folded into `status`. A caller bug must never be indistinguishable from an exchange-rule rejection.

## Verification

- Instantiate `KoreaExchangeKrxApiEngine`. Reproduce the KRX-published band: `get_daily_price_limit_bounds(9_940)` must return the amount KRW 2,980 and the band KRW 6,960 – KRW 12,920, and an order at exactly KRW 12,920 must be accepted while KRW 12,930 returns `PRICE_LIMIT_EXCEEDED`.
- Confirm the current tick schedule: KRW 1,500 → KRW 1, KRW 15,000 → KRW 10, KRW 150,000 → KRW 100. All three differ from the pre-2023 schedule.
- Confirm class sensitivity: KRW 15,005 must return `KRX_ORDER_VALIDATED` under `security_class="ETF_ETN"` and `INVALID_TICK_SIZE` under `STOCK`.
- Confirm code handling: `03473K` must validate; `00781I` must raise (excluded letter); `5930` must raise unless the engine was built with `allow_zero_pad=True`.
- Confirm input guards: a zero base price, a NaN price, `side="BYU"`, and `quantity=0` must each raise `ValueError` rather than returning a report.
- Run the test suite:
```bash
python -m unittest discover -s skills/korea-exchange-krx-api-integration/scripts
```

## Related Skills

- `japan-exchange-group-jpx-api-integration`
- `exchange-tick-size-regime-tracking`
- `taiwan-stock-exchange-twse-api`
- `minimum-fill-size-and-lot-rounding-logic`
- `global-exchange-holiday-calendar-handling`
