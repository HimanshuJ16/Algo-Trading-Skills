# Deployment and Sign-off Checklist — Algo Parameter Defaults by Liquidity Tier

## Prerequisites

- [ ] Document ADV unit, lookback, session calendar, corporate-action treatment, and as-of timestamp.
- [ ] Configure `high_adv_threshold > medium_adv_threshold > 0`.
- [ ] Configure a maximum permitted ADV age and a versioned calibration identifier.
- [ ] Define all three validated tier profiles and confirm profile keys match their tiers.
- [ ] Confirm EMS support for independent spread, volatility, venue, price, credit, position, notional, and kill-switch controls.

## Validation

- [ ] Run `python -m unittest discover -s skills/algo-parameter-defaults-by-instrument-liquidity-tier/scripts`.
- [ ] Reject negative, NaN, infinite, stale, and mixed-unit ADV values.
- [ ] Verify inclusive threshold boundaries and deterministic tier classification.
- [ ] Verify profiles are immutable and calibration versions are persisted.
- [ ] Verify `requires_live_market_check=True` for spread-crossing-capable profiles.
- [ ] Verify custom profile validation and incomplete-tier rejection.

## Deployment

- [ ] Load only an approved configuration checksum and calibration version.
- [ ] Apply profiles as starting caps, not routing authorization.
- [ ] Recheck current spread/depth, quote freshness, volatility, order size, venue state, and independent risk limits before every child order.
- [ ] Record tier, ADV value/age, profile version, market snapshot, decision, and order identifiers.
- [ ] Enable monitoring for shortfall, fill rate, participation, spread cost, rejects, cancellations, residual quantity, and overrides.

## Rollback and Recovery

- [ ] Preserve the last approved calibration and threshold/profile set.
- [ ] Pause affected instruments on stale ADV, data-quality breach, or unexpected tier migration.
- [ ] Reconcile open orders and preserve audit events before switching calibration versions.
- [ ] Roll back on TCA deterioration or risk-policy breach through an approved change process.
- [ ] Require fresh ADV, validated market data, and review before resuming.

## Post-Deployment Verification

- [ ] Compare live execution quality against calibration benchmarks by tier and instrument.
- [ ] Review implementation shortfall, fill rate, participation, spread crossing cost, and residual risk.
- [ ] Confirm no order crossed solely because of an ADV tier.
- [ ] Review data age, corporate-action adjustments, tier migrations, and risk overrides.
- [ ] Record reviewer, code version, data version, calibration version, and sign-off date.

## Sign-off

- Execution Quant: ___________________________
- Risk/Compliance: ___________________________
- Date: ___________________________