# Deep Workflow Reference — walk-forward-hyperparameter-search-budget

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Validate the Grid**:
   - Reject an empty grid, and reject any axis with no candidate values. An empty axis
     makes $N_{\text{raw}} = 0$, which compares as within budget and produces a
     LOW-risk report for a search that evaluates nothing.
   - Reject a bare string as an axis: `{"mode": "abc"}` is a sized sequence and would
     silently expand into three one-character candidates.

2. **Compute Max Allowed Budget**:
   - $N_{\text{max}} = \mathrm{clamp}(\lfloor (T_{\text{in}}/252) \times R \rfloor, 10, 500)$,
     with $R$ = `max_trials_per_year` (default 100).
   - Reject $T_{\text{in}} \le 0$. Clamping a non-positive window up to the floor of 10
     reports an allowance for a window that does not exist.

3. **Audit Grid Combination Size**:
   - Calculate the raw Cartesian grid size $N_{\text{raw}} = \prod K_i$ from the axis
     cardinalities alone. Do not enumerate the product to measure it.

4. **Prune / Sample Grid**:
   - If $N_{\text{raw}} > N_{\text{max}}$, select $N_{\text{max}}$ distinct flat indices
     from $[0, N_{\text{raw}})$ with a seeded RNG, sort them, and decode each to a
     combination by mixed-radix division.
   - Decoding must match `itertools.product` ordering: the last axis varies fastest, so
     its stride is 1 and each earlier axis's stride is the product of all cardinalities
     to its right.
   - **Do not use a constant stride.** `combinations[::k]` aliases against that same
     mixed-radix layout. On a $20 \times 20 \times 10$ grid pruned to 100 the stride is
     40, and because $40$ is a multiple of the last axis's cardinality the sweep
     explores exactly one of its ten values, and five of the second axis's twenty.
     Every budget assertion still passes.
   - Sampling on indices keeps memory proportional to the budget, not to
     $N_{\text{raw}}$: a $20^{10}$ grid prunes in the same space as a grid of 50.

5. **Audit Cumulative Evaluations**:
   - Sum `sampled_evaluations` across every window report.
   - Compare against $N_{\text{max}}$ recomputed on the **total distinct span** of the
     campaign — first in-sample day to last out-of-sample day. Overlapping walk-forward
     windows reuse the same data; summing window lengths inflates the allowance by the
     overlap factor.

6. **Report Overfitting Risk**:
   - Classify LOW / MODERATE / HIGH on the overrun ratio, with HIGH above
     `HIGH_RISK_OVERRUN_MULTIPLE` (5) times the budget.
   - The per-window grade describes the grid **as designed** ($N_{\text{raw}}$ against
     $N_{\text{max}}$), not the pruned search that ran. A HIGH grade on a pruned search
     means the space was mis-scaled for the data, which pruning does not change.

7. **Cross-Check Against MinBTL**:
   - Compute the Minimum Backtest Length implied by the campaign's total trial count and
     compare it to the span actually available.
   - Treat a shortfall as a finding even when the house budget passes. See
     `references/standards.md` for the formula, its published anchor, and the
     independence caveat that makes the result conservative.

## Production Implementation Reference

- Reference code: `scripts/search_budgeter.py`
  (`HyperparameterSearchBudgeter`, `SearchBudgetReport`, `WalkForwardBudgetAudit`,
  `expected_max_sharpe`, `minimum_backtest_length_years`, `SearchBudgetError`).
- Automated unit tests: `scripts/test_search_budgeter.py`.
