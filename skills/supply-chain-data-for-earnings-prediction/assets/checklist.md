# Pre-Flight Checklist

Sign off before a supply-chain read-through signal is allowed to influence capital.

## Units

- [ ] Is `consensus_revenue_growth_pct` a **revenue** growth consensus, not an EPS
      consensus? (The engine cannot detect the difference; the number will look fine
      either way.)
- [ ] Are the supplier growth, customer inventory growth and consensus all on the
      same period-over-period convention (normally YoY, same fiscal quarter) and the
      same percentage units?
- [ ] Are the growth rates like-for-like — organic vs. reported, constant vs. current
      currency, and 52/53-week calendar effects reconciled?

## Point-in-time

- [ ] Is `available_from_iso` the **publication instant** (earnings release or SEC
      filing acceptance), never the fiscal period end?
- [ ] Have the filing deadlines been accounted for — 10-Q at 40 / 40 / 45 days and
      10-K at 60 / 75 / 90 days after period end by filer category — rather than
      assuming a figure is usable on the period end date?
- [ ] Are all timestamps timezone-aware ISO-8601, with naive values rejected rather
      than assumed UTC?
- [ ] Is an explicit `as_of_iso` passed on every call, in research and in production?
- [ ] Is `max_observation_age_days` set to a defensible bound — and is it understood
      that leaving it `None` makes `stale_observations_excluded_count` read zero
      because nothing was checked?
- [ ] Have `future_observations_excluded_count` and
      `stale_observations_excluded_count` been inspected on a real batch?

## Graph and weights

- [ ] Are **both** supplier concentration figures populated and not confused —
      `supplier_share_of_target_cogs_pct` (the weight) and
      `target_share_of_supplier_revenue_pct` (the read-through screen)?
- [ ] Is `supplier_coverage_pct` inspected on every evaluation, and understood as the
      honest bound on the estimate rather than a diagnostic?
- [ ] Is it documented that the filings-derived graph is truncated — no ASC 280
      disclosure below 10% of revenues, no customer identity required, and Item
      101(c) naming dropped by SEC Release 33-10825 from 9 November 2020 — so
      coverage is not comparable across eras?
- [ ] Do the weights sum to at most 100% on each side, with no duplicated links?
- [ ] Has the vendor's licence position been established for storing and trading on
      this relationship data?

## Statistics

- [ ] Is `consensus_dispersion_pct` an **estimated** dispersion — cross-analyst
      dispersion for this company-quarter, or the historical standard deviation of
      this model's own realized gap — rather than a fixed assumption?
- [ ] Is `surprise_z_threshold` calibrated against the *same* dispersion definition
      that is being fed in?
- [ ] Are `supplier_blend_weight` and `inventory_blend_weight` re-estimated against
      realized target revenue growth rather than left at the illustrative 0.70 / 0.30?
- [ ] Is `min_read_through_share_pct` calibrated for this sector, and demonstrably
      not lowered to make a thin chain produce signals?
- [ ] Is `min_supplier_coverage_pct` calibrated, and demonstrably not lowered for the
      same reason?

## Consumption

- [ ] Does the downstream consumer distinguish `INSUFFICIENT_DATA` from `NEUTRAL`, and
      refuse to act on the former?
- [ ] Is `surprise_z_score is None` rendered as "not measurable" and never coerced
      to 0?
- [ ] Is a supplier-only evaluation (no usable customer observations) understood as an
      absence of inventory measurement, not as measured zero channel build?
- [ ] Is the full `SupplyChainEarningsSignal` record persisted **with the
      configuration used**, so the Z-score is reproducible?
- [ ] Is this one input among several, with sizing, stops and exposure limits owned
      elsewhere?

## Scope

- [ ] Is it documented that `BUY_EARNINGS_SURPRISE` is a directional bias on a
      fundamental estimate and not an order instruction?
- [ ] Is it understood that the engine applies no industry or macro control — a
      supplier growing 20% in a sector growing 25% scores positive?
- [ ] Is `generate_signals` excluded from every production path, and understood to be
      a deprecated placeholder that multiplies a column by 1.5?
- [ ] If any input derives from shipping manifests, logistics feeds or expert
      networks rather than public filings, have the MNPI controls in
      `insider-trading-controls-for-alternative-data-usage` been applied?
