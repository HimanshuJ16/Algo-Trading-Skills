---
name: exchange-tick-size-regime-tracking
description: >-
  Use when a limit price is constructed programmatically and must be a legal increment
  on the destination venue: SEC Rule 612 penny and sub-penny bands, and the MiFID II RTS
  11 price-by-liquidity grid.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: tick-size, exchange-rules, mifid-ii-rts-11, sec-rule-612, dfm-tick, price-alignment, market-data
  brokers_frameworks: "SEC Rule 612 (17 CFR 242.612); MiFID II RTS 11 (EU) 2017/588; DFM Circular 02/2026; Python Decimal"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in market-data pipelines, Smart Order Routers, and order-entry gateways whenever a limit price is constructed programmatically — from a signal, a mid-price, a spread offset, or a repricing loop — and must be a legal increment on the destination venue before it is sent. An off-tick price is rejected by the matching engine (or, on a venue that silently rounds, filled at a price you did not choose), and on a regulated venue an impermissibly fine quote is a rule breach, not just a bad fill.

The three regimes shipped here are structurally different, and that difference is the point of the skill:

- **`US_EQUITIES` — SEC Rule 612 (17 CFR 242.612).** Price-driven: $\ge \$1.00 \implies \$0.01$, $< \$1.00 \implies \$0.0001$.
- **`EU_RTS11`** (alias `EU_XETRA`) **— MiFID II RTS 11.** *Not* price-driven. The tick is a cell in a 19 price ranges $\times$ 6 liquidity bands table; the liquidity band comes from the instrument's average daily number of transactions (ADNT) published by ESMA/the NCA.
- **`DFM_DUBAI` — DFM Circular 02/2026** (effective 2026-04-06). Five AED price bands, applying to listed equities, ETFs and REITs.

## When NOT to Use

- **To validate an execution price.** Rule 612 governs *displaying, ranking, or accepting* bids, offers, orders and indications of interest. It does not govern executions: a midpoint fill at \$10.005 or a sub-penny price improvement is lawful. Auditing a fill price against this engine will flag legitimate executions as violations.
- **As a substitute for venue reference data.** Every table here is a regulatory *minimum*. RTS 11 requires ticks "equal to or greater than" the Annex value, and venues may publish coarser ticks per instrument or per segment. When the venue's own tick is known, pass it as `venue_assigned_tick`; the engine will use it and reject it only if it is finer than the regulatory floor.
- **For instruments outside each regime's scope.** RTS 11 covers shares, depositary receipts and ETFs on EU trading venues — not bonds, structured products or derivatives, which carry venue-specific step tables. Rule 612 covers NMS stocks, not options, futures or crypto.
- **For venues that are not registered.** The engine raises `UnknownVenueError` rather than defaulting to a penny. Register the venue with `register_venue()` from its published rulebook first.
- **For quantity/lot rounding** — see `minimum-fill-size-and-lot-rounding-logic`.

## Prerequisites

- Venue identifier (`US_EQUITIES`, `EU_RTS11` / `EU_XETRA`, `DFM_DUBAI`) or a regime registered via `register_venue()`.
- Proposed limit price, ideally as `str` or `Decimal`. Floats are accepted and read through their shortest repr, so `0.1 + 0.2` is treated as the `0.30000000000000004` it actually is.
- **For RTS 11 venues only:** the instrument's liquidity band 1–6, from the ESMA/NCA annual ADNT calculation. `liquidity_band_for_adnt()` maps a published ADNT to a band. ETFs whose underlyings are exclusively in-scope shares use band 6 (RTS 11 Article 3).
- Order `side` if the price will be sent as a live limit order (see step 2).

## Workflow

1. **Resolve the active tick size** — `get_active_tick_size_decimal(venue, price, liquidity_band=..., tick_constrained=...)`.
   - **Decision point — is the venue band-dependent?** An RTS 11 venue queried without `liquidity_band` raises `LiquidityBandRequiredError`. Do not paper over it with a default: at €25 the tick ranges from €0.005 (band 6) to €0.2 (band 1), a factor of 40. Guessing the liquid band under-ticks 5 of the 6 bands.
   - **Decision point — is this a tick-constrained US symbol?** The amended Rule 612 \$0.005 increment is assigned per symbol by the listing exchange from a Time Weighted Average Quoted Spread $\le \$0.015$; it cannot be inferred from price, and it is **not yet operative** (SEC exemptive relief of 2026-06-11 defers compliance to the first business day of November 2027). Pass `tick_constrained=True` only when carrying a real assignment from reference data.
   - **Decision point — do you hold the venue's own tick?** If so pass `venue_assigned_tick`; the regulatory table is a floor, not the venue's authoritative step.

2. **Choose a rounding policy before aligning** — `align_price_to_tick_decimal(price, tick, side=..., policy=...)`.
   - `PASSIVE` (BUY rounds down, SELL rounds up) is the correct default for live limit orders: it can never push a buy limit above the price the strategy asked for, and never turns a resting quote into a spread-crossing taker.
   - `NEAREST` (round half up) is for reference/analytics prices where no order is being sent.
   - `AGGRESSIVE` is a deliberate marketable reprice and *will* pay more or receive less than proposed.
   - A price smaller than half a tick raises rather than aligning to zero or silently multiplying the limit.

3. **Audit and re-check the band** — `audit_order_tick_compliance(...)` returns a `TickRegimeAuditReport`.
   - Alignment can move a price *across* a band boundary: \$0.99999 rounds to \$1.0000, where the minimum increment becomes \$0.01 rather than \$0.0001. The engine re-resolves the tick at the aligned price, reports the tick that actually governs the price being sent, and sets `crossed_price_band`.
   - `status` is one of `TICK_COMPLIANT`, `OFF_TICK_ALIGNED` (auto-aligned, `auto_align=True`), or `OFF_TICK_REJECTED` (`auto_align=False`; nothing is sent, and `aligned_price_decimal` shows what a legal price would have been).
   - **Decision point — send `aligned_price_decimal`, not `aligned_price`.** The float mirror exists for logging and legacy callers; the `Decimal` is the exact value the gateway should serialise.

4. **Record the audit trail.** The report carries `regulatory_source`, `liquidity_band`, `side`, `rounding_policy` and both exact `Decimal` values so a compliance reviewer can reconstruct why a price was changed.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating RTS 11 as a price-only table.** The single most common error in EU order entry: a hardcoded price→tick map ignores the liquidity dimension entirely and is wrong for five of the six bands. It fails *silently* — the engine returns a plausible number and the venue rejects the order (or accepts an illegally fine quote).
- **Auditing fills against Rule 612.** Sub-penny *executions* from midpoint matching or price improvement are permitted; only quotes, orders and IOIs are constrained. Flagging a \$10.0050 midpoint print as a breach generates false compliance alerts.
- **Rounding a buy limit up.** Half-up rounding of \$150.005 gives \$150.01 — one cent above the instruction, on every share, and potentially through the offer. Use `PASSIVE` with a `side` for anything that will be routed.
- **Assuming the aligned price is still in the same band.** \$0.99999 aligns to \$1.0000 and leaves the sub-penny regime; a table lookup performed only on the pre-alignment price reports a \$0.0001 tick for a price that must now be a multiple of \$0.01.
- **Defaulting an unmapped venue to \$0.01.** A silent fallback produces off-tick orders on every venue with a finer or coarser step, and the failure surfaces as unexplained rejections in production, not at configuration time.
- **Float arithmetic in the price path.** `0.1 + 0.2` is not `0.3`, and a tolerance wide enough to hide that (e.g. `1e-6`) is 1% of a \$0.0001 tick — wide enough to wave through genuinely off-tick sub-dollar prices. Carry prices as `str`/`Decimal` end to end.
- **Hardcoding a regime and never revisiting it.** DFM widened the top of its table on 2026-04-06 (AED 100+ moved to a 0.10 step) and validated *existing open orders* against the new rules on the effective date. Tick tables change by circular; treat them as versioned reference data.
- **Reading the US \$0.005 tier as live.** It is adopted but under exemptive relief until the first business day of November 2027, and it is a per-symbol assignment — quoting a half-penny on an unassigned symbol is a Rule 612 breach.

## Verification

- Instantiate `ExchangeTickSizeRegimeEngine()`. Query `US_EQUITIES` at \$150.00 $\implies$ \$0.01; at \$0.50 $\implies$ \$0.0001; at exactly \$1.00 $\implies$ \$0.01.
- Query `EU_RTS11` at €25.00 with `liquidity_band=1` $\implies$ €0.2 and with `liquidity_band=6` $\implies$ €0.005 (RTS 11 Annex, row $20 \le P < 50$). Query it *without* a band and confirm `LiquidityBandRequiredError`.
- Query `DFM_DUBAI` at AED 150.05 $\implies$ tick AED 0.10, `is_on_tick` false, aligned AED 150.10 (Circular 02/2026).
- Audit \$150.005 on `US_EQUITIES`: default `NEAREST` $\implies$ `OFF_TICK_ALIGNED` at \$150.01; `side='BUY', policy='PASSIVE'` $\implies$ \$150.00; `auto_align=False` $\implies$ `OFF_TICK_REJECTED`.
- Audit \$0.99999 on `US_EQUITIES` and confirm `aligned_price_decimal == Decimal('1.00')`, `active_tick_size_decimal == Decimal('0.01')`, `crossed_price_band` true.
- Negative checks: unknown venue, NaN/inf/zero/negative price, non-positive tick, `PASSIVE` without a side, a `venue_assigned_tick` finer than the regulatory floor, and a registered table with a gap must each raise.
- Run `python -m unittest discover -s skills/exchange-tick-size-regime-tracking/scripts` and confirm 100% pass rate.

## Related Skills

- `minimum-fill-size-and-lot-rounding-logic`
- `deutsche-borse-xetra-api-integration`
- `post-only-limit-repricing-under-fast-markets`
- `tick-size-pilot-program-impact-assessment`
- `reference-data-golden-source-designation`
