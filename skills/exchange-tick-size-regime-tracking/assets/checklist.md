# Pre-Flight / Sign-off Checklist — exchange-tick-size-regime-tracking

## Venue regime configuration
- [ ] Every destination venue is registered from its **published rulebook or circular**, with the effective date recorded.
- [ ] No default/fallback tick exists anywhere in the price path — an unmapped venue fails loudly (`UnknownVenueError`).
- [ ] Each registered table starts at 0, is contiguous and ordered, has strictly positive ticks, and is unbounded above.
- [ ] Tick tables are tracked as versioned reference data with a re-verification cadence (they change by circular).

## Regime correctness
- [ ] US: $\ge \$1.00 \implies \$0.01$, $< \$1.00 \implies \$0.0001$ (SEC Rule 612).
- [ ] US: the amended $\$0.005$ tick-constrained increment is **off by default** and only enabled from a real per-symbol assignment (compliance deferred to the first business day of November 2027).
- [ ] EU: the RTS 11 liquidity band (1–6) is sourced from the ESMA/NCA ADNT publication — never inferred from price, never defaulted.
- [ ] EU: ETFs whose underlyings are exclusively in-scope instruments use band 6 (RTS 11 Article 3).
- [ ] DFM: the AED 100+ band uses a 0.10 step (Circular 02/2026, effective 2026-04-06).
- [ ] Where venue reference data publishes an instrument's own tick, it is passed as `venue_assigned_tick` (regulatory tables are minimums).

## Price alignment
- [ ] Prices are carried as `str`/`Decimal` through the order path; no float arithmetic decides tick compliance.
- [ ] On-tick is an exact zero remainder — no absolute epsilon tolerance.
- [ ] Live limit orders use `PASSIVE` rounding with an explicit `side` (BUY down / SELL up); `NEAREST` is reserved for analytics.
- [ ] The tick reported and logged is the one governing the **aligned** price; `crossed_price_band` is surfaced when alignment changed band.
- [ ] `aligned_price_decimal` (not the float mirror) is what the order gateway serializes.

## Order-entry integration
- [ ] Pre-trade gates that may not alter a client price run with `auto_align=False` and treat `OFF_TICK_REJECTED` as a block.
- [ ] `OFF_TICK_ALIGNED` deltas are logged and the strategy is known to tolerate the repriced level.
- [ ] Resting orders are re-audited when a venue changes its tick regime, not just new orders.
- [ ] Fill/execution prices are **not** audited against Rule 612 — sub-penny price improvement and midpoint prints are lawful.

## Audit trail
- [ ] Reports retain `regulatory_source`, `liquidity_band`, `side`, `rounding_policy` and both `Decimal` values.
- [ ] Off-tick rejections and alignments are logged at warning/error level for post-trade review.

## Testing
- [ ] Automated Testing: Run `python -m unittest discover -s skills/exchange-tick-size-regime-tracking/scripts` — 100% pass rate.
- [ ] Negative paths covered: unknown venue, missing liquidity band, NaN/inf/zero/negative price, non-positive tick, directional policy without a side, finer-than-regulatory `venue_assigned_tick`, gapped table.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
