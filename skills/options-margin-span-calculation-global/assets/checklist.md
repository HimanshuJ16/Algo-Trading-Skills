# Pre-Flight / Sign-off Checklist — options-margin-span-calculation-global

Use this before considering the skill's implementation complete.

- [ ] **SPAN Scenario Matrix:** Confirm `evaluate_scenario_grid()` scans price ($\pm 6\%$) and volatility ($\pm 15\%$) shocks.
- [ ] **Defined-Risk Netting:** Confirm defined-risk spreads (Iron Condors) net offsetting legs and cap margin at max loss.
- [ ] **Naked Option Exposure Penalty:** Confirm unhedged short options incur additional exposure margin additions.
- [ ] **Intraday Margin Monitoring:** Confirm margin utilization is tracked dynamically against live capital.
- [ ] **Automated Testing:** Run `python scripts/test_span_approx.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
