# Checklist for Arrival Price Algorithms

- [ ] Confirm `UrgencyLevel.HIGH` heavily front-loads child orders in the first 25% of the time horizon.
- [ ] Confirm `UrgencyLevel.LOW` generates a uniform (flat) execution schedule.
- [ ] Confirm the sum of all child orders in the trajectory perfectly equals the parent order size.
- [ ] Run test suite: `python scripts/test_arrival_price_benchmark_execution_algo.py`.

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________