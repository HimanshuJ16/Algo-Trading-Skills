# Deep Workflow Reference — walk-forward-hyperparameter-search-budget

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Compute Max Allowed Budget**:
   - $N_{\text{max}} = \text{years} \times 100$, capped between $10$ and $500$.
2. **Audit Grid Combination Size**:
   - Calculate raw Cartesian grid size $N_{\text{raw}} = \prod K_i$.
3. **Prune / Sample Grid**:
   - If $N_{\text{raw}} > N_{\text{max}}$, apply systematic step sampling to enforce $N_{\text{evals}} \le N_{\text{max}}$.
4. **Report Overfitting Risk**:
   - Classify risk level (LOW, MODERATE, HIGH) based on budget overrun ratio.

## Production Implementation Reference

- Reference code: `scripts/search_budgeter.py` (`HyperparameterSearchBudgeter`, `SearchBudgetReport`).
- Automated unit tests: `scripts/test_search_budgeter.py`.
