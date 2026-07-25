# Pre-Flight / Sign-off Checklist — vectorized-vs-event-driven-backtest-tradeoffs

Use this before considering the skill's implementation complete.

- [ ] **Complexity Scoring:** Confirm strategy characteristics score optimal engine architecture.
- [ ] **Vectorized Engine Execution:** Confirm $O(N)$ vectorized matrix returns are evaluated cleanly.
- [ ] **Event-Driven Fidelity:** Confirm discrete order events and fill mechanics are simulated.
- [ ] **Execution Drag Quantification:** Confirm Sharpe divergence and return drag are calculated.
- [ ] **Automated Testing:** Run `python scripts/test_engine_selector.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
