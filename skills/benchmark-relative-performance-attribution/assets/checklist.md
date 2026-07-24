# Pre-Flight / Sign-off Checklist — benchmark-relative-performance-attribution

Use this before considering the skill's implementation complete.

- [ ] **Alpha & Beta Calculation:** Confirm `evaluate_alpha_beta()` computes Beta and CAPM Alpha correctly.
- [ ] **Information Ratio:** Confirm $IR = \text{Active Return} / TE$ is computed accurately.
- [ ] **Brinson Sector Attribution:** Confirm Allocation, Selection, and Interaction effects sum to total active return.
- [ ] **Sign-off Criteria:** Confirm $\alpha > 0$ and $IR \ge 0.50$ for production strategy sign-off.
- [ ] **Automated Testing:** Run `python scripts/test_attribution_engine.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
