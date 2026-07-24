# Pre-Flight / Sign-off Checklist — explainability-for-live-trading-signals

Use this before considering the skill's implementation complete.

- [ ] **Feature Contribution Summation:** Confirm $\text{BaseValue} + \sum \phi_i = \hat{Y}$ prediction score.
- [ ] **Driver Ranking:** Confirm top bullish and bearish feature drivers are extracted accurately.
- [ ] **Natural Language Summary:** Confirm generated summary includes action, score, and key drivers.
- [ ] **JSON Audit Serialization:** Confirm `to_json_audit()` creates valid JSON string.
- [ ] **Automated Testing:** Run `python scripts/test_signal_explainer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
