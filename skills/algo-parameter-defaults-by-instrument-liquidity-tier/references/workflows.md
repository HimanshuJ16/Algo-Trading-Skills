# Workflows for Algo Parameter Defaults by Instrument Liquidity Tier

## Production Execution Pipeline

1. **Build the ADV observation**: Calculate or ingest ADV with instrument identity, units, session calendar, lookback, corporate-action treatment, and as-of timestamp.
2. **Validate freshness and quality**: Reject missing, negative, non-finite, stale, split-inconsistent, or mixed-unit ADV. Do not default to the lowest tier on data failure.
3. **Select a versioned manager**: Load approved thresholds and profiles; record `calibration_version` and configuration checksum.
4. **Classify and retrieve**:
   ```python
   profile = manager.get_profile(adv, adv_age_days=adv_age_days)
   ```
5. **Apply independent pre-trade gates**: Check current spread/depth, quote freshness, volatility, order size, venue status, price bands, parent quantity, notional, credit, and position limits.
6. **Configure the scheduler**: Apply the profile as a starting cap for TWAP, VWAP, or IS. Recalculate child size against remaining parent quantity and current executable liquidity.
7. **Gate spread crossing**: If `profile.cross_spread_allowed` is true, require `profile.requires_live_market_check` and a current EMS decision. A tier lookup alone cannot authorize a marketable order.
8. **Monitor execution**: Record profile version, tier, ADV age, market snapshot, child decisions, fills, rejects, cancellations, and risk overrides.
9. **Reconcile and retune**: Compare implementation shortfall, fill rate, spread cost, participation, residual quantity, and signaling metrics by tier. Retune only through approved walk-forward/TCA governance.

## Data-Failure Workflow

1. Detect stale/missing ADV, corporate-action inconsistency, or unit mismatch.
2. Stop new profile-driven routing for the affected instrument or use an explicitly approved fallback policy outside this manager.
3. Preserve the last known-good calibration and data-quality evidence.
4. Recompute ADV and reconcile corporate actions before resuming.
5. Record the override, reviewer, timestamps, and configuration version.

## Calibration Workflow

1. Define the objective and benchmark: arrival price, implementation shortfall, VWAP, spread cost, or another approved metric.
2. Split calibration and evaluation periods chronologically; do not tune on future observations.
3. Evaluate thresholds and profiles by instrument, session, volatility regime, order size, and venue.
4. Stress low-volume, halted, wide-spread, news, and capacity-constrained scenarios.
5. Approve a versioned profile set only when risk limits and TCA results meet policy.
6. Deploy with monitoring, rollback, and post-deployment review.

## Failure Handling Matrix

| Failure | Manager behavior | Required integration behavior |
|---|---|---|
| Negative, NaN, or infinite ADV | Raises validation error | Reject data and prevent new routing. |
| Stale ADV | Raises validation error when age is supplied | Refresh or use an approved external fallback; do not silently classify LOW. |
| Invalid threshold/profile configuration | Rejects construction | Block deployment/config reload and retain last approved version. |
| High-tier spread crossing | Returns capability plus live-check requirement | EMS evaluates current quotes, impact, venue, and risk before crossing. |
| Volatility/news/liquidity withdrawal | Manager does not detect it | Apply a live market-state overlay or pause the parent order. |
| TCA deterioration | No automatic retune | Trigger governed calibration review and rollback if required. |

## Rollback Workflow

1. Trigger rollback on data-quality breach, risk override increase, excessive shortfall, or profile-policy violation.
2. Stop new assignments for the affected calibration version.
3. Restore the last approved thresholds/profiles and verify checksum.
4. Reconcile open parent/child orders and preserve audit events.
5. Review affected fills and rerun walk-forward evaluation before redeployment.