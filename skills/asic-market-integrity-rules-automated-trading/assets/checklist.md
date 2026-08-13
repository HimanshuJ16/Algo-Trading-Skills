# Checklist for ASIC AOP Compliance

- [ ] Confirm `AsicKillSwitchManager` is wired to an immediate, manual-override button (API endpoint or dashboard).
- [ ] Confirm pre-trade filters actively *reject* orders rather than just logging warnings (RG 241.35 outcome (d)).
- [ ] Confirm price deviation logic uses a valid, real-time reference price; zero/stale reference prices are rejected, not divided by.
- [ ] Confirm non-finite (NaN/Inf) inputs in price, qty, or reference price are rejected before any limit comparison.
- [ ] Confirm `AsicMarketIntegrityConfig` limits are positive and finite; a misconfigured (non-positive/NaN) limit would silently disable a mandatory control.
- [ ] Confirm every `ComplianceResult` (approved and rejected) is persisted with `rejection_code`, `order_id` and `checked_at_unix` for the ASIC audit trail.
- [ ] Confirm kill-switch trigger/reset events are recorded in `audit_log` with timestamp, reason and actor.
- [ ] Confirm filter parameters can only be changed via administrator-level direct control (Rule 5.6.3(2)) and that such changes are themselves audited.
- [ ] Run test suite: `python scripts/test_asic_market_integrity_rules_automated_trading.py`.

## Sign-off
- Compliance Officer: ___________________________
- Date: ___________________________
