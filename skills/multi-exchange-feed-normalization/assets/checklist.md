# Pre-Flight / Sign-off Checklist — multi-exchange-feed-normalization

Use this before considering the skill's implementation complete.

- [ ] **Unified Data Model:** Confirm all parsed ticks return standard `UnifiedTick` objects.
- [ ] **Symbol Mapping:** Confirm venue tickers map to canonical symbols via `register_symbol_mapping()`.
- [ ] **Timestamp Standardization:** Confirm exchange timestamps are coerced to float epoch seconds.
- [ ] **Side Normalization:** Confirm buyer/seller maker flags map to `NormalizedSide` enum.
- [ ] **Automated Testing:** Run `python scripts/test_feed_normalizer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
