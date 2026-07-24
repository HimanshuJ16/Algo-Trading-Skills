# Deep Workflow Reference — margin-utilization-circuit-breaker

## Full Procedure

1. Query broker for used margin, available margin, and account equity.
2. Compute utilization ratio: used_margin / equity.
3. Compare against warning (60%) and hard stop (80%) thresholds.
4. For pre-trade checks, add projected additional margin to current used margin.

## Production Implementation Reference

- Code: `scripts/margin_breaker.py` (`MarginUtilizationBreaker`).
- Tests: `scripts/test_margin_breaker.py`.
