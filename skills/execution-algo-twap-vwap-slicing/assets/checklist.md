# Pre-Flight / Sign-off Checklist — execution-algo-twap-vwap-slicing

Use this before considering the skill's implementation complete.

- [ ] **Benchmark Alignment:** Confirm algorithm selection (TWAP or VWAP) matches strategy volume profile expectations.
- [ ] **Jitter & Quantity Conservation:** Confirm child order sizes and timestamps include randomized jitter while maintaining exact total quantity ($\sum \text{sizes} = \text{total\_qty}$).
- [ ] **Partial Fill Rescheduling:** Confirm partial fills or order rejections trigger dynamic recalculation of remaining pending child orders via `CatchUpPolicy`.
- [ ] **Execution Quality Report:** Confirm `get_execution_report()` outputs VWAP achieved price and benchmark slippage in basis points post-execution.
- [ ] **Automated Testing:** Run `python scripts/test_slicer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
