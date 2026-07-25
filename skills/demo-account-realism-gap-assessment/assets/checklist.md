# Pre-Flight / Sign-off Checklist — demo-account-realism-gap-assessment

Use this before considering the skill's implementation complete.

- [ ] **Execution Log Ingestion:** Confirm demo and live execution logs are captured with timestamps, arrival prices, fill prices, and fill quantities.
- [ ] **Metric Calculation:** Confirm mean latency (ms), mean slippage (bps), and fill rates are calculated accurately.
- [ ] **Realism Score Computation:** Confirm Realism Score $R$ is bounded between 0.0 and 1.0.
- [ ] **Sharpe Haircut Application:** Confirm demo Sharpe ratio is scaled down by $R$.
- [ ] **Automated Testing:** Run `python scripts/test_realism_assessor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
