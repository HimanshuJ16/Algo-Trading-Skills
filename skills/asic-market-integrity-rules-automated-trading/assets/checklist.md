# Checklist for ASIC AOP Compliance

- [ ] Confirm `AsicKillSwitchManager` is wired to an immediate, manual-override button (API endpoint or dashboard).
- [ ] Confirm pre-trade filters actively *reject* orders rather than just logging warnings.
- [ ] Confirm price deviation logic uses a valid, real-time reference price.
- [ ] Run test suite: `python scripts/test_asic_market_integrity_rules_automated_trading.py`.

## Sign-off
- Compliance Officer: ___________________________
- Date: ___________________________