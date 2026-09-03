---
name: philippine-stock-exchange-api
description: >-
  Use when validating equity orders for the Philippine Stock Exchange, keying board lot
  and tick size off the security's reference price and enforcing the asymmetric daily
  price band before entry.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: pse, philippine-stock-exchange, psei, xts, board-lot, tick-size, static-threshold, dynamic-threshold, asian-markets
  brokers_frameworks: "PSEtrade XTS Protocol; PSE Revised Trading Rules Article IV; Python Decimal; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when placing or validating equity orders on the Philippine Stock Exchange (PSE / PSEi) via PSEtrade XTS or a broker API (COL Financial, BDO Securities, First Metro Securities, DragonFi and others). PSE rejects an order at entry on three independent grounds, and all three are checkable client-side:

- a **board lot** that the quantity does not divide by (PSE's lots run from 1,000,000 shares for sub-centavo issues down to 5 shares for issues above PHP 5,000),
- a **price fluctuation (tick)** the limit price does not sit on, and
- a limit price outside the **static** or **dynamic** threshold band.

The rule that catches most integrations out is that the board lot and the tick are **not** functions of the order price. Article IV Section 8 keys both off the security's **Reference Price** — the previous session's close, or the Last Adjusted Closing Price (LACP) after a corporate action — and fixes them for the whole trading day.

## When NOT to Use

- **As a substitute for exchange-side controls.** This is a client-side pre-trade filter. The PSE matching engine is authoritative and can reject an order this engine approves.
- **For session mechanics.** The pre-open and pre-open-no-cancel phases, the market recess, pre-close, Run-Off / Trading-at-Last and the closing VWAP session each have their own order-acceptance behaviour. None of it is modelled here.
- **For halts, suspensions and the market-wide circuit breaker.** These change whether the order trades at all; they do not change the lot, the tick or the band.
- **For the Odd Lot Market.** Quantities below one board lot — typically the residue of a stock dividend — trade on a separate board. This engine flags them as `INVALID_BOARD_LOT` for the main board; it does not route them.
- **As a source of dynamic threshold percentages.** PSE assigns each security a trade-frequency cluster by circular and reviews it semi-annually. The percentage must come from that circular; the engine will not guess one, and skips the check when you do not supply it.
- **For cost, tax or settlement modelling.** Broker commission, VAT, the SEC fee, the PSE transaction fee, the stock transaction tax on sales, and the T+2 settlement cycle are all out of scope.

## Prerequisites

- A PSE order request: `symbol`, `side` (`BUY`/`SELL`), `price`, `quantity` in shares, and `reference_price`.
- The security's **Reference Price** for the trading day — the previous session's closing price, or the LACP where a corporate action intervened. This single number fixes the board lot, the tick size and the static band. It is not the order price and not the last traded price.
- The listing's **market segment** — `PHP` (peso-denominated, the main board) or `DDS` (dollar-denominated). It selects the schedule and must come from reference data.
- Optionally, for the dynamic threshold: the **last traded price** and the security's **published dynamic threshold percentage**.
- Integration config (`api_key`, `environment`).

## Workflow

1. **Resolve the Reference Price and market segment.** Both are properties of the security, taken from reference data. Getting the segment wrong is silent: USD 1.50 demands a 20-share lot on the DDS table and a 1,000-share lot on the peso one.
2. **Board Lot & Tick Size lookup — from the Reference Price, never the order price.** Article IV Section 8: *"The Board Lot and Price Fluctuation of a Security for any Trading Day shall be based on the Security's Reference Price."* Bands are published with explicit `From`/`To` columns and both bounds are inclusive:
   - $\text{PHP } 0.0001 - 0.0099 \implies \text{Tick } 0.0001, \text{Lot } 1{,}000{,}000$
   - $\text{PHP } 0.5000 - 4.9900 \implies \text{Tick } 0.0100, \text{Lot } 1{,}000$
   - $\text{PHP } 5.0000 - 9.9900 \implies \text{Tick } 0.0100, \text{Lot } 100$
   - $\text{PHP } 50.0000 - 99.9500 \implies \text{Tick } 0.0500, \text{Lot } 10$
   - $\text{PHP } \ge 5{,}000 \implies \text{Tick } 5.0000, \text{Lot } 5$
   - Full 15-band table in `references/standards.md`.
3. **Static Threshold audit — asymmetric $+50\% / -30\%$.** CN-2020-0028 cut the lower threshold from 50% to 30% effective **24 March 2020**; the band has not been symmetric since.
   - $\text{Ceiling} = \operatorname{floor_{tick}}(1.50 \times P_{\text{ref}})$, $\text{Floor} = \operatorname{ceil_{tick}}(0.70 \times P_{\text{ref}})$, both on the **Reference Price's** tick.
   - Round the ceiling *down* and the floor *up*: rounding the floor down would publish a bound representing a fall of more than 30%.
   - Both bounds are **inclusive** — an order at exactly the ceiling is the ceiling price and trades.
4. **Dynamic Threshold audit — only when you have the published percentage.** The band is symmetric about the **last traded price**, at 20% / 15% / 10% for trade-frequency clusters A / B / C. Supply `last_traded_price` and `dynamic_threshold_pct` together or omit both; supplying one alone raises rather than silently skipping the check.
5. **Order divisibility and tick alignment.** $\text{quantity} \bmod \text{board\_lot} = 0$, and the price an exact `Decimal` multiple of the tick. Never scale-and-round.
6. **Audit report generation.** Emit a `PSEReport` carrying the applied lot and tick, the Reference Price, and **both** band bounds, so a rejection can be repriced rather than merely reported.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deriving the board lot or tick from the order price.** Article IV Section 8 keys both off the Reference Price. A stock whose Reference Price is PHP 4.90 trades on a 1,000-share lot and a PHP 0.01 tick *all day*, including for orders priced at PHP 5.05. Reading the tier from the order price accepts a 100-share order the exchange rejects. PSE's own worked example makes this unambiguous: at a Reference Price of PHP 1,642.00 the ceiling is PHP 2,463.00 — a price that is **not** a multiple of the PHP 2.00 tick belonging to the band PHP 2,463.00 itself falls in.
- **Carrying a symmetric $\pm 50\%$ static band.** The lower static threshold has been **30%**, not 50%, since 24 March 2020 (CN-2020-0028). A validator still using −50% passes an order priced 40% below the Reference Price that PSE rejects on entry. Widely republished third-party material still shows the old symmetric figure.
- **Checking only the static threshold.** An order can sit comfortably inside the ±band and still be rejected by the **dynamic threshold** against the last traded price. With a last traded price of PHP 100.00 in a cluster-C security (10%), PHP 120.00 is inside the static band of PHP 70.00 – PHP 150.00 and outside the dynamic band of PHP 90.00 – PHP 110.00.
- **Testing tick alignment by scaling and rounding.** `round(price * 10000) % round(tick * 10000)` rounds the sub-tick remainder away: PHP 1,000.00005 scales to 10,000,000.5, rounds to 10,000,000, and passes as a clean multiple of PHP 1.00. Compare with `Decimal` modulo.
- **Computing the band in binary floating point.** `0.30 * 1.50` is `0.4499999999999999`, so an order at exactly the PHP 0.45 ceiling is rejected as a band breach. The error is one part in $10^{16}$ and it lands precisely on the price where orders cluster.
- **Reporting a data fault as a rule breach.** A NaN Reference Price makes every `<=` comparison return `False`, so a naive validator emits `PRICE_BAND_BREACH` for what is a missing-data bug. Validate that prices are finite and strictly positive first, and *raise* on malformed input rather than folding it into a status.
- **Submitting odd lots to the main board.** 50 shares of a stock whose Reference Price is PHP 8.00 (100-share lot) is rejected on the main board or routed to the illiquid Odd Lot Market. Note that PSE has circulated a consultation paper proposing to remove the Odd Lot Market entirely — see below.
- **Hard-coding a board lot table that PSE has told you will change.** Consultation Paper CN-2025-0046 (15 December 2025) proposes a **One Lot One Share** structure and the removal of the Odd Lot Market, alongside the migration from PSEtrade XTS to **Nasdaq Eqlipse Trading** in 2026. It is a *proposal*, not in force, and the tick bands it proposes differ from today's. Inject a replacement schedule through the engine's `schedules` argument when it takes effect rather than forking the module.
- **Applying the peso schedule to a dollar-denominated security.** DDS listings have their own board lot table. USD 1.50 needs a 20-share lot there and would be validated against a 1,000-share lot on the peso table.

## Verification

- Reproduce PSE's published worked example: `get_pse_tier(Decimal("1642.00"))` must return `(Decimal("1.0000"), 5)` and `get_static_threshold_bounds(Decimal("1642.00"))` must return `(Decimal("1150"), Decimal("2463"))` — the floor is the raw PHP 1,149.40 rounded **up** to the PHP 1.00 tick.
- Confirm the asymmetric band: a Reference Price of PHP 100.00 gives PHP 70.00 – PHP 150.00, and an order at PHP 60.00 must return `PRICE_BAND_BREACH`. Under the pre-2020 symmetric band it was compliant.
- Confirm the Reference Price governs the lattice: with `reference_price=Decimal("4.90")`, an order of 100 shares at PHP 5.05 must return `INVALID_BOARD_LOT` (lot 1,000), and 1,000 shares must return `ORDER_VALID_COMPLIANT`.
- Confirm dynamic threshold layering: reference PHP 100.00, last traded PHP 100.00, `dynamic_threshold_pct=10`, order at PHP 120.00 must return `DYNAMIC_THRESHOLD_BREACH` with `is_within_price_band` still `True`.
- Confirm exact arithmetic: `reference_price=Decimal("0.30")` with an order at PHP 0.45 must return `ORDER_VALID_COMPLIANT`, and PHP 1,000.00005 against a PHP 1,000.00 reference must return `INVALID_TICK_SIZE`.
- Confirm input guards: `side="LONG"`, `quantity=0`, `quantity=True`, a NaN price and a NaN reference price must each raise `ValueError` rather than returning a report.
- Run the test suite:
```bash
cd skills/philippine-stock-exchange-api/scripts
python -m unittest discover -s skills/philippine-stock-exchange-api/scripts
```

## Related Skills

- `korea-exchange-krx-api-integration`
- `japan-exchange-group-jpx-api-integration`
- `singapore-exchange-sgx-api-integration`
