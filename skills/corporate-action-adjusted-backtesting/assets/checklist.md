# Pre-Flight / Sign-off Checklist — corporate-action-adjusted-backtesting

Use this before considering the skill's implementation complete.

- [ ] **Split Adjustment:** Confirm pre-split prices are halved and volumes doubled for 2:1 splits.
- [ ] **Reverse Split Adjustment:** Confirm pre-reverse split prices are multiplied and volumes reduced.
- [ ] **Dividend Cash Credit:** Confirm `calculate_dividend_cash_credit()` credits cash to open positions on ex-dates.
- [ ] **Double-Adjustment Prevention:** Confirm raw unadjusted data is validated prior to adjustment.
- [ ] **Automated Testing:** Run `python scripts/test_corporate_action_adjuster.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
