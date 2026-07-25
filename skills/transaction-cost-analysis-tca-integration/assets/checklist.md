# Pre-Flight / Sign-off Checklist — transaction-cost-analysis-tca-integration

Use this before considering the skill's implementation complete.

- [ ] **Implementation Shortfall Tracking:** Confirm total $IS$ is evaluated in basis points (bps).
- [ ] **Cost Component Decomposition:** Confirm delay, spread cross, market impact, and commissions are isolated.
- [ ] **Sqrt Market Impact Model:** Confirm market impact scales with $\sqrt{\text{Size}/\text{ADV}}$.
- [ ] **Net Return Calibration:** Confirm backtest returns reflect total TCA friction drag.
- [ ] **Automated Testing:** Run `python scripts/test_tca_integrator.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
