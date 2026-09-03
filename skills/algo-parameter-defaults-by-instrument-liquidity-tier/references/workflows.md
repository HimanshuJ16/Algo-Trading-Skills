# Workflows for Algo Parameter Defaults by Instrument Liquidity Tier

## Production Execution Pipeline

1. **Build the ADV observation**: Calculate or ingest ADV with instrument identity, units, session calendar, lookback, corporate-action treatment, and as-of timestamp.
2. **Validate freshness and quality**: Reject missing, negative, non-finite, stale, split-inconsistent, or mixed-unit ADV. Do not default to the lowest tier on data failure. A zero ADV is a data failure, not a `LOW` instrument.
3. **Select a versioned manager**: Load approved thresholds and profiles; record `calibration_version` and configuration checksum. Set `require_adv_age=True` unless an approved policy allows classification without a verified observation age — the freshness check is otherwise skipped whenever `adv_age_days` is omitted.
4. **Classify and retrieve**:
   ```python
   manager = ExecutionParameterManager(
       high_adv_threshold=..., medium_adv_threshold=...,
       calibration_version=approved_version,
       max_adv_age_days=..., require_adv_age=True,
   )
   profile = manager.get_profile(adv, adv_age_days=adv_age_days)
   ```
   Read the calibrated set, when needed for reporting or reconciliation, through the read-only `manager.profiles` mapping proxy. Never mutate a calibration in place.
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
| Zero ADV | Logs a warning and classifies `LOW` | Treat the warning as a routing stop; the manager cannot tell "never trades" from "the feed broke". |
| Stale ADV | Raises validation error when age is supplied | Refresh or use an approved external fallback; do not silently classify LOW. |
| Missing ADV age | Raises only when `require_adv_age=True`; otherwise skips the freshness check entirely | Enable `require_adv_age` or enforce the as-of timestamp upstream. An omitted age is unchecked, not fresh. |
| Invalid threshold/profile configuration | Rejects construction | Block deployment/config reload and retain last approved version. |
| Invalid `ExecutionProfile` field | Rejects construction of the profile itself, not only of the manager | Fail the calibration build; a profile that constructs has already satisfied every documented invariant. |
| Attempt to mutate a live calibration | `manager.profiles` is a read-only proxy and raises `TypeError` | Build and approve a new manager; never patch a running calibration. |
| High-tier spread crossing | Returns capability plus live-check requirement | EMS evaluates current quotes, impact, venue, and risk before crossing. |
| `LOW`-tier IS profile with `cross_spread_allowed=False` | Returns the passive posture; the manager has no urgency model | Supply an explicit escalation policy (who crosses, on what residual, by when) or the parent order accrues unbounded timing risk. |
| Volatility/news/liquidity withdrawal | Manager does not detect it | Apply a live market-state overlay or pause the parent order. |
| TCA deterioration | No automatic retune | Trigger governed calibration review and rollback if required. |

## Rollback Workflow

1. Trigger rollback on data-quality breach, risk override increase, excessive shortfall, or profile-policy violation.
2. Stop new assignments for the affected calibration version.
3. Restore the last approved thresholds/profiles and verify checksum.
4. Reconcile open parent/child orders and preserve audit events.
5. Review affected fills and rerun walk-forward evaluation before redeployment.