# Checklist for Algo Wheel Implementation

- [ ] Ensure `decision_price` is captured at the precise moment the strategy triggers, before any network latency to the broker.
- [ ] Ensure explicit commissions, SEC fees, and exchange fees are included in the IS calculation.
- [ ] Verify Buy and Sell sides use correctly inverted IS math.
- [ ] Ensure the worst-performing broker retains a minimum canary allocation (e.g., 10%).
- [ ] Run test suite: `python scripts/test_algo_wheel_broker_execution_quality_comparison.py`.

## Sign-off
- Head of Trading: ___________________________
- Date: ___________________________
