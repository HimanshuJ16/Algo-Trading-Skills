# Pre-Flight / Sign-off Checklist — order-book-depth-processing-l2-l3

Use this before considering the skill's implementation complete.

- [ ] **Thread-Safety:** Confirm all book state modifications are protected by mutex locks.
- [ ] **Crossed-Book Guard:** Confirm `is_crossed` is set to `True` when $\text{Best Bid} \ge \text{Best Ask}$.
- [ ] **Imbalance Calculation:** Confirm book imbalance ratio is bounded within $[-1.0, +1.0]$.
- [ ] **L3 Order Lifecycle:** Confirm L3 order additions and cancellations re-aggregate price level volumes.
- [ ] **Automated Testing:** Run `python scripts/test_depth_processor.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
