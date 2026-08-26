# Workflows for Market Data Cost Optimization

1. **Split the invoice before modelling anything**:
   - Separate the symbol-metered component (per-query, per-instrument, symbol-slot,
     provisioned feed-handler/storage capacity) from the fixed component (per-firm
     access, per-subscriber entitlements, non-display licences, connectivity).
   - Identify where per-symbol charges cap. A capped metered charge stops yielding
     savings once you are under the cap, so the addressable saving is bounded.
   - If the metered component is immaterial, stop here and report that. This skill
     cannot move fixed fees.

2. **Assemble per-symbol activity state as of the audit date**:
   - `has_active_position`, `has_active_signal`, `days_since_last_trade`
     (`None` = never traded), `days_in_current_tier`.
   - `has_active_signal` must reflect *current* strategy state, not a historical flag.
     A stale signal input is the direct cause of demoting a live name.

3. **Classify each symbol, safety-first**:
   - Position **and** signal $\implies$ `TIER1_DIRECT_L3`.
   - Position **or** signal $\implies$ at least `TIER2_SIP_L1`.
   - Fill within the inactivity threshold $\implies$ `TIER2_SIP_L1`.
   - Otherwise $\implies$ `TIER3_DELAYED_EOD`.

4. **Reject malformed input instead of defaulting it**:
   - Unrecognised tier, duplicate symbol, blank symbol, negative day counts, non-bool
     activity flags, and a non-finite or negative cost all raise.

5. **Apply the demotion dwell guard**:
   - Withhold a demotion for a symbol that has not been in its current tier for
     `min_days_before_demotion` days. Fees are not prorated, so an intra-period
     demotion saves nothing and the re-promotion buys a fresh full period.
   - Never withhold a promotion.

6. **Price the change and report both denominators**:
   - Symbol-metered: `baseline_monthly_spend_usd`, `optimized_monthly_spend_usd`,
     `savings_percentage`.
   - Total: `baseline_total_monthly_spend_usd`,
     `optimized_total_monthly_spend_usd`,
     `total_savings_percentage_including_fixed`. Quote this one externally.
   - Status is `COST_OPTIMIZATION_SUCCESS`, `NO_SAVINGS_FOUND`, or
     `NET_COST_INCREASE`. A net increase driven by required promotions is a correct
     outcome, not a failure.

7. **Execute and reconcile**:
   - Apply the recommendations in the entitlement system (DACS / EMRS or the vendor
     console) — the engine changes nothing by itself.
   - Check that each promotion is covered by the relevant venue licence and
     subscriber classification before it is applied.
   - Reconcile the next invoice against the projected optimized spend. A projection
     that does not show up on the invoice usually means the fee was fixed, capped,
     or not prorated — feed that back into step 1.
