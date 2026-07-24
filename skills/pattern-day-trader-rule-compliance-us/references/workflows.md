# Deep Workflow Reference — pattern-day-trader-rule-compliance-us

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Rolling 5-Business-Day Day Trade Tracking:**
   - Maintain `PDTComplianceEngine` tracking day trades across rolling 5-business-day calendar windows (excluding weekends and NYSE holidays).

2. **Same-Day Open-Close Trade Classification:**
   - Automatically classify position openings and closings: same-day open-then-close orders are classified as `DayTradeRecord`; overnight positions held past market close are excluded.

3. **Sub-$25,000 Equity Veto Gate:**
   - Query `would_breach_pdt(current_equity)` prior to placing any 4th day-trade order within the rolling 5-business-day window. Veto execution if account equity $< \$25,000$.

4. **Broker Day-Trade Count Reconciliation:**
   - Reconcile local day-trade counter against broker API reported day-trade counters via `reconcile_broker_count()`.

## Failure Modes Observed in Production

- **Unmonitored 4th Day Trade:** Placing a 4th day trade on an under-threshold margin account, triggering a 90-day FINRA day-trading restriction.
- **Calendar-Day vs Business-Day Miscalculation:** Counting rolling 5 calendar days instead of 5 business days, miscalculating window expiration dates.
- **Overnight Trade Misclassification:** Misclassifying overnight positions as day trades or vice versa.
- **Unreconciled Local Counter:** Relying purely on internal trade counts without validating against broker-reported day-trade counts.

## Production Implementation Reference

- Reference code: `scripts/pdt_tracker.py` (`PDTComplianceEngine`, `DayTradeRecord`, `TradeExecution`).
- Automated unit tests: `scripts/test_pdt_tracker.py`.
