# Pre-Flight / Sign-off Checklist — forex-broker-integration-oanda-mt5

Use this before considering the skill's implementation complete.

- [ ] **Environment Isolation:** Confirm practice and live use separate hosts, tokens and account IDs, sourced from separate configuration — not a runtime flag on a shared client. `oanda_hosts()` refuses to default; confirm nothing wraps it in one that does.
- [ ] **Pip Size From Broker Metadata:** Confirm pip size comes from OANDA `pipLocation` or MT5 `symbol_info().digits` via `InstrumentSpec`, not from the instrument's name.
- [ ] **No Guessed Pip Sizes:** Confirm an instrument with no metadata — a metal, index or crypto CFD — raises rather than falling back to a default pip size.
- [ ] **Pip Value Currency Conversion:** Confirm pip value is converted from the instrument's quote currency into the account currency. Cross-check by hand against one JPY pair and one non-JPY pair: 1 standard lot USD/JPY at 150.00 in a USD account is ≈ 6.67 USD per pip, not 1,000.
- [ ] **Lot to Units Conversion:** Confirm position sizing uses exact `lots_to_units()` translations, and that `LOT_SIZES` matches the broker's stated contract size.
- [ ] **Overnight Swap Modeling:** Confirm `SwapRolloverCalculator` is loaded with the broker's *published* swap rates in their stated units, and that an unconfigured pair or side raises rather than accruing an assumed rate.
- [ ] **Triple-Swap Rollover:** Confirm the triple-swap weekday is derived from the instrument's settlement convention (`triple_swap_weekday()` — Wednesday for T+2, Thursday for T+1 pairs such as USD/CAD), and that multi-week holds are charged one triple-swap rollover per week via `count_triple_swap_rollovers()`.
- [ ] **Weekly Close Boundary:** Confirm the Friday close / Sunday open boundary is resolved through a timezone database against the broker's server timezone, not stored as a UTC constant.
- [ ] **MT5 Bridge Liveness Check:** Confirm MT5 terminal connection health is monitored independently of the Python process via `MT5BridgeMonitor`, that the check function is explicitly supplied, and that a `None` `terminal_info()` or a raising probe reports unhealthy.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/forex-broker-integration-oanda-mt5/scripts` and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
