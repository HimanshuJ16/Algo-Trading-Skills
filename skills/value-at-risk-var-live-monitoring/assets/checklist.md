# Pre-Flight / Sign-off Checklist — value-at-risk-var-live-monitoring

Use this before considering the skill's implementation complete.

- [ ] **Parametric VaR Calculation:** Confirm $Z_{0.99} \cdot \sigma_p - \mu_p$ is calculated accurately.
- [ ] **Historical Simulation VaR:** Confirm empirical $1\text{st}$ percentile quantile cutoff is extracted.
- [ ] **CVaR / Expected Shortfall:** Confirm average loss beyond VaR cutoff is computed.
- [ ] **Live Risk Breaker:** Confirm orders are blocked when 1-day VaR exceeds $5.0\%$ NAV limit.
- [ ] **Automated Testing:** Run `python scripts/test_var_monitor.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
