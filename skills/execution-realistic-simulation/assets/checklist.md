# Pre-Flight / Sign-off Checklist — execution-realistic-simulation

Use this before considering the skill's implementation complete.

- [ ] **Bid-Ask Spread Direction:** Confirm BUY fills occur at/above Ask price and SELL fills occur at/below Bid price.
- [ ] **Square-Root Market Impact:** Confirm market impact scales non-linearly with order size relative to ADV.
- [ ] **Complete Fee Stack Verification:** Confirm statutory fees (STT, Exchange Txn Fees, SEBI fees, GST, Stamp Duty) match published exchange fee cards.
- [ ] **Partial Fill Handling:** Confirm order sizes exceeding top-of-book depth produce partial fills.
- [ ] **Automated Testing:** Run `python scripts/test_fill_model.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
