# Pre-Flight / Sign-off Checklist — forex-broker-integration-oanda-mt5

Use this before considering the skill's implementation complete.

- [ ] **Pip Precision Verification:** Confirm JPY pairs ($0.01$) and standard pairs ($0.0001$) use correct pip sizes via `ForexPipEngine`.
- [ ] **Lot to Units Conversion:** Confirm position sizing uses exact `lots_to_units()` translations.
- [ ] **Overnight Swap Modeling:** Confirm overnight financing and Wednesday 3x triple-swap rules are integrated via `SwapRolloverCalculator`.
- [ ] **MT5 Bridge Liveness Check:** Confirm MT5 terminal connection health is monitored independently via `MT5BridgeMonitor`.
- [ ] **Automated Testing:** Run `python scripts/test_pip_conversion.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
