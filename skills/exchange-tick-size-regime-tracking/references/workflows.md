# Deep Workflow Reference — exchange-tick-size-regime-tracking

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

0. **Establish the venue regime**:
   - Resolve the venue with `resolve_venue()` / any lookup call. An unregistered venue
     raises `UnknownVenueError` — there is deliberately no default tick, because a
     silent $0.01 fallback produces off-tick orders wherever the real step is finer or
     coarser and surfaces only as unexplained rejections in production.
   - Register additional venues from their published rulebook with `register_venue()`.
     Tables are validated at registration: they must start at price 0, be contiguous and
     ordered, carry strictly positive ticks, and be unbounded above. A table that stops
     at the highest price seen so far is exactly how a venue ends up quoted at a stale
     increment when a stock rallies past the top band.

1. **Resolve the active tick size** — `get_active_tick_size_decimal(venue, price, ...)`:
   - **Band-dependent venues (RTS 11).** The tick is a cell in a 19 × 6 matrix. The
     liquidity band comes from the ADNT published by ESMA/the NCA, applied from the
     annual application date; `liquidity_band_for_adnt()` maps a published ADNT to a
     band. Querying without a band raises `LiquidityBandRequiredError` rather than
     assuming the liquid band — at €25 the tick spans €0.005 (band 6) to €0.2 (band 1).
   - **US tick-constrained symbols.** The amended Rule 612 $0.005 increment is assigned
     per symbol from a Time Weighted Average Quoted Spread $\le \$0.015$ and is under
     exemptive relief until the first business day of November 2027. `tick_constrained`
     defaults to `False`; pass `True` only when carrying a genuine assignment, and only
     for `US_EQUITIES` (any other venue raises).
   - **Venue reference data wins.** Every table here is a regulatory minimum. Pass the
     venue's published step as `venue_assigned_tick` when it is known; a value coarser
     than the regulatory floor is accepted, a finer one is rejected because it would
     breach the regime it claims to satisfy.

2. **Align the price** — `align_price_to_tick_decimal(price, tick, side=..., policy=...)`:
   - Arithmetic is exact: `divmod(Decimal(price), Decimal(tick))`. A price is on-tick
     when the remainder is exactly zero. No epsilon is used — an absolute tolerance such
     as `1e-6` is 1% of a $0.0001 tick and wide enough to pass genuinely off-tick
     sub-dollar prices.
   - Float inputs are converted through `str()`, i.e. their shortest round-tripping
     repr. `0.1 + 0.2` is therefore read as `0.30000000000000004` and aligned, not
     silently treated as `0.3`. Carry prices as `str`/`Decimal` where possible.
   - Policies:
     - `NEAREST` — round half up; ignores side. For analytics and reference prices.
     - `PASSIVE` — BUY rounds down, SELL rounds up. **The correct choice for live limit
       orders**: it can never lift a buy limit above the instructed price or push a
       resting quote across the spread into taker fees.
     - `AGGRESSIVE` — BUY rounds up, SELL rounds down. A deliberate marketable reprice.
   - `PASSIVE`/`AGGRESSIVE` without a `side` raise; there is no safe direction to guess.
   - A price below half a tick raises rather than aligning to zero (an invalid order
     price) or silently multiplying the limit up to one tick.

3. **Re-check the price band after alignment**:
   - Alignment can move a price across a band boundary. $0.99999 rounds to $1.0000,
     where Rule 612's increment becomes $0.01 instead of $0.0001; a tick resolved only
     on the pre-alignment price is then wrong for the price actually being sent.
   - `audit_order_tick_compliance()` re-resolves the tick at the aligned price and
     repeats alignment until price and governing tick agree (bounded to four passes,
     after which an oscillating table raises rather than looping). `crossed_price_band`
     is set when the governing band changed.
   - With a `venue_assigned_tick`, a crossing that lands in a band whose regulatory
     minimum is coarser than the assigned tick raises — the override has become illegal
     at the new price.

4. **Act on the audit report**:
   - `TICK_COMPLIANT` — send `aligned_price_decimal` (identical to the proposal).
   - `OFF_TICK_ALIGNED` (`auto_align=True`) — the price was moved; log the delta, and
     confirm the strategy tolerates the new price before routing. On a `PASSIVE` buy the
     move is always in the strategy's favour; on `NEAREST` it may not be.
   - `OFF_TICK_REJECTED` (`auto_align=False`) — nothing is sent;
     `aligned_price_decimal` shows what a legal price would have been. Use this mode in
     pre-trade gates where changing a client's price is not permitted.
   - Serialize `aligned_price_decimal`, not the float mirror. The floats exist for
     logging and legacy callers.

5. **Maintain the tables as versioned reference data**:
   - Tick tables change by circular and by rule amendment (DFM widened its top band on
     2026-04-06 and revalidated open orders on the effective date). Track the effective
     date alongside the table, and re-run the audit over resting orders when a regime
     changes — not only over new ones.
   - Every report carries `regulatory_source`, so a compliance reviewer can reconstruct
     which regime version priced an order.

## Scope boundaries

- Rule 612 constrains display/ranking/**acceptance** of quotes, orders and IOIs — not
  executions. Do not audit fill prices with this engine: lawful midpoint and
  price-improved prints are sub-penny by design.
- RTS 11 covers shares, depositary receipts and ETFs on EU venues. Bonds, structured
  products and derivatives use venue-specific tables; register them explicitly.
- Quantity rounding (board lots, `MinQty`) is a separate concern — see
  `minimum-fill-size-and-lot-rounding-logic`.

## Production Implementation Reference

- Reference code: `scripts/exchange_tick_size_regime_tracking.py`
  (`ExchangeTickSizeRegimeEngine`, `VenueTickRegime`, `PriceBandTickRule`,
  `TickRegimeAuditReport`, `TickRoundingPolicy`, `RTS11_TICK_TABLE`).
- Automated unit tests: `scripts/test_exchange_tick_size_regime_tracking.py`, including
  RTS 11 Annex cell assertions across liquidity bands, DFM Circular 02/2026 band
  regressions, band-boundary alignment, and directional rounding invariants.
