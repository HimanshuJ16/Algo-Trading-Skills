---
name: idx-indonesia-stock-exchange-api
description: >-
  Quantitative market gateway engine for Indonesia Stock Exchange (IDX / BEI JATS system), enforcing 4-letter tickers, IDX Fraksi Harga tick sizes anchored to the previous close, 100-share Board Lots, and the asymmetric ARA/ARB Auto Rejection bands.
domain: Global Market Integration & FX
subdomain: Southeast Asian Market Connectivity & IDX Gateway
tags: ["idx", "indonesia-exchange", "bei", "jats-system", "fraksi-harga", "board-lot", "arb-ara"]
brokers_frameworks: ["JATS (Jakarta Automated Trading System)", "IDX FIX Gateway", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in Southeast Asian market execution algorithms, IDX gateways, and JATS trading systems. Trading on the Indonesia Stock Exchange (IDX / BEI) requires strict adherence to JATS order rules: 4-letter ticker symbols (e.g. `BBCA` Bank Central Asia, `TLKM` Telkom), market segment designations (`RG` Pasar Reguler, `TN` Pasar Tunai, `NG` Pasar Negosiasi), **Fraksi Harga** tick sizes, mandatory **100-share Board Lot** multiples, a per-order volume cap, and the **asymmetric ARA/ARB Auto Rejection** band.

## When NOT to Use

- **Non-equity IDX instruments**: rights (`TLKM-R`), warrants (`TLKM-W`), structured warrants, ETFs, bonds and derivatives have their own trading rules and are deliberately rejected by the ticker validator.
- **As a replacement for exchange-side controls**: this is a client-side pre-trade filter. JATS remains authoritative and can reject an order this engine approves — trading halts, suspensions, short-sale restrictions, and the pre-opening/closing auction phases are not modelled.
- **Intraday tick re-derivation**: do not call the engine with a live last-traded price in `reference_price`. The Fraksi Harga and Auto Rejection band are both anchored to the previous close for the whole trading day.

## Prerequisites

- IDX order request (`ticker`, `board_type`: `RG`/`TN`/`NG`, `side`, `price`, `quantity`, `reference_price`).
- A reference price (Acuan Harga) source: the previous Pasar Reguler closing price, or the theoretical price after a corporate action, or the listing price on a debut.
- Optional: the instrument's listing board (`MAIN`, `DEVELOPMENT`, `NEW_ECONOMY`, `ACCELERATION`, `WATCHLIST`) and listed share count, for the correct Auto Rejection percentages and the 5%-of-listed-shares volume cap.
- Current IDX Peraturan Nomor II-A schedules — see `references/standards.md` for the dated tables in force.

## Workflow

1. **Ticker & Market Segment Normalization**:
   - Format ticker to 4 uppercase ASCII letters (`BBCA`, `TLKM`); reject suffixed instruments (`-R`, `-W`).
   - Validate market segment (`RG`, `TN`, `NG`) and order side (`BUY`, `SELL`).
   - Validate that `reference_price` is finite and strictly positive **before** using it as a divisor or band anchor — a zero or missing previous close must raise, never silently produce a band of zero width.
2. **Segment Branch — decide which rules even apply**:
   - `RG` / `TN` trade on the continuous JATS order book: round lot, Fraksi Harga, volume cap and Auto Rejection all apply.
   - `NG` (Pasar Negosiasi) is bilaterally negotiated: **none of those four apply**. Do not reject a negotiated block for being an odd lot or for pricing outside the ARA/ARB band — that is a false rejection of a legitimate trade.
3. **IDX Fraksi Harga (Tick Size) Audit**:
   - Select the tick from the **reference price**, not the order price. IDX fixes the tick for a full trading day from the previous close and only re-derives it on the next trading day:
     - $P_{ref} < \text{Rp } 200 \implies \Delta P = \text{Rp } 1$.
     - $\text{Rp } 200 \le P_{ref} < \text{Rp } 500 \implies \Delta P = \text{Rp } 2$.
     - $\text{Rp } 500 \le P_{ref} < \text{Rp } 2,000 \implies \Delta P = \text{Rp } 5$.
     - $\text{Rp } 2,000 \le P_{ref} < \text{Rp } 5,000 \implies \Delta P = \text{Rp } 10$.
     - $P_{ref} \ge \text{Rp } 5,000 \implies \Delta P = \text{Rp } 25$.
   - Verify the order price is a whole number of Rupiah and an exact integer multiple of $\Delta P$.
4. **Minimum Price Floor**: reject prices below Rp 50 on the Main/Development/New Economy boards, or below Rp 1 on the Acceleration/Watchlist boards.
5. **Board Lot Sizing (1 Lot = 100 Shares)**: for `RG` and `TN`, verify `quantity` is a multiple of $100$ shares.
6. **Order Volume Auto Rejection**: reject orders larger than $\min(50{,}000 \text{ lots},\ 5\% \text{ of listed shares})$. With no listed-share count available, enforce the 50,000-lot cap and record that the 5% leg was not evaluated.
7. **Price Auto Rejection (ARA / ARB) Audit** — asymmetric since 8 April 2025:
   - Main/Development/New Economy: ARA $= +35\%$ ($P_{ref} \le \text{Rp } 200$), $+25\%$ ($\text{Rp } 200 < P_{ref} \le \text{Rp } 5{,}000$), $+20\%$ ($P_{ref} > \text{Rp } 5{,}000$); ARB $= -15\%$ in every band.
   - Acceleration/Watchlist: $\pm \text{Rp } 1$ for $P_{ref} \le \text{Rp } 10$, else $\pm 10\%$.
   - Clamp the lower bound to the minimum price floor.
8. **Audit Report Generation**: Output structured `IdxOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deriving the tick from the order price**: IDX fixes the Fraksi Harga from the previous closing price for the whole trading day. With a Rp 1,990 close (tick Rp 5), Rp 2,005 is a valid price all day even though its own band implies Rp 10; with a Rp 2,010 close (tick Rp 10), Rp 1,995 is *invalid* all day. Deriving the tick from the order price produces false rejections in one direction and orders JATS will reject in the other.
- **Treating ARA and ARB as symmetric**: since 8 April 2025 ARB is a flat 15% while ARA is 20–35% by price band. A symmetric $\pm 25\%$ check simultaneously waves through sell orders IDX will auto-reject and blocks buy orders IDX would accept.
- **Applying order-book rules to Pasar Negosiasi**: `NG` is exempt from round lots, Fraksi Harga, the volume cap and Auto Rejection. Rejecting a negotiated block for an odd lot or an off-band price blocks a legitimate trade.
- **Ignoring the listing board**: an Acceleration or Watchlist (Papan Pemantauan Khusus) stock has a $\pm 10\%$ band and a Rp 1 floor, not the main-board 35/25/20% and Rp 50. Applying main-board limits to a Watchlist name mis-sizes the band by a factor of two or more.
- **Unvalidated reference price**: a missing or zero previous close divides by zero or collapses the Auto Rejection band. Raise on it; never fall back to the order price.
- **Submitting invalid tickers**: 5-letter, numeric, or suffixed codes (`TLKM-R`) cause instant gateway rejection.
- **Odd-lot submission to the Regular market**: routing non-100-share multiples to `RG`/`TN`.
- **Forgetting the per-order volume cap**: an order above 50,000 lots (or 5% of listed shares, whichever is smaller) is auto-rejected on size alone, regardless of price.

## Verification

- Instantiate `IdxStockExchangeApiEngine`. Route a Bank Central Asia order (`ticker="BBCA"`, `board_type="RG"`, Price $=\text{Rp } 10,000$, Qty $=500$ shares / 5 Lots, Ref Price $=\text{Rp } 10,000$). Verify the engine validates the 4-letter ticker, selects $\Delta P = \text{Rp } 25$ from the reference price, confirms the 5-lot size, computes the asymmetric band $\text{Rp } 8{,}500 - \text{Rp } 12{,}000$, and returns `IDX_ORDER_VALIDATED`.
- Confirm the reference-price anchoring: an order at Rp 2,005 against a Rp 1,990 reference price must be **accepted** (tick Rp 5), and an order at Rp 1,995 against a Rp 2,010 reference price must be **rejected** with `INVALID_TICK_SIZE` (tick Rp 10).
- Confirm asymmetry: against a Rp 1,000 reference price, Rp 1,200 (+20%) is accepted while Rp 800 (−20%) returns `AUTO_REJECTION_EXCEEDED`.
- Run the test suite:
```bash
cd skills/idx-indonesia-stock-exchange-api/scripts
python -m unittest test_idx_indonesia_stock_exchange_api.py
```

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `philippine-stock-exchange-api`
- `singapore-exchange-sgx-api-integration`
