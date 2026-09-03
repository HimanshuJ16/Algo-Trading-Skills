# Deep Workflow Reference — backtest-parameter-sensitivity-analysis

This file holds the full technical procedure referenced by `SKILL.md`. The metric
definition, verdict ladder and sourcing live in `references/standards.md`.

## Full Procedure

1. **Characterise your backtest's noise before choosing a threshold.**
   Re-run one configuration several times. If Sharpe moves run-to-run, the degradation
   threshold must sit above that dispersion or it measures the simulator rather than
   the strategy. A deterministic backtest removes the question entirely — see
   `backtest-determinism-and-reproducibility`.

2. **Choose the range before the resolution.**
   The grid must bracket the optimum. A monotonically improving parameter yields an
   `EDGE_OPTIMUM` verdict, which is a statement about the range you swept, not about
   the strategy. Widen and re-run. Resolution is the lesser concern and costs you
   trials: every extra point inflates the maximum Sharpe further.

3. **Configure the analyzer.**
   ```python
   an = ParameterSensitivityAnalyzer(
       max_neighborhood_degradation_pct=0.15,  # calibrate to step 1
       min_viable_sharpe=0.3,                  # your deployment hurdle, not 0.0
       min_grid_points=3,                      # structural minimum
   )
   ```

4. **Sweep.**
   ```python
   results = an.run_grid_sweep("lookback", range(10, 111, 5), backtest_fn)
   ```
   `param_values` need not be sorted, but must be unique and finite. A non-finite
   Sharpe from `backtest_fn` raises rather than entering the grid: NaN compares False
   against everything, so an unguarded NaN would sail through the plateau test and
   emerge as a robust verdict.

5. **Analyze.**
   ```python
   report = an.analyze_sensitivity(results, "lookback")
   if report.verdict is not RobustnessVerdict.ROBUST_PLATEAU:
       ...
   ```
   Grid points are ordered by parameter value inside the call, so list order cannot
   influence the result. Switch on `report.verdict` rather than substring-matching
   `report.message`.

6. **Read the verdict as a screen, not a certificate.**
   `ROBUST_PLATEAU` means the optimum survived a one-step perturbation of one
   parameter, in-sample. It is one input to a deployment decision.

7. **Deflate the headline number.**
   Carry `report.total_grid_points` forward as the trial count and correct
   `report.best_sharpe` for selection bias before quoting it as an expectation. This
   analyzer deliberately does not do it — see `factor-research-multiple-testing-correction`.

8. **Repeat per parameter, then check the joint surface.**
   Each parameter screened independently can look like a plateau while the joint
   surface is a knife edge. One-dimensional passes narrow the search; they do not
   settle it.

9. **Record the sweep.**
   Persist the grid definition, every point's Sharpe, the analyzer configuration and
   the verdict. The trial count is only meaningful if you also record the sweeps you
   ran and discarded.

## Known Failure Modes

- **A losing strategy certified as a plateau.** When the degradation term is only
  computed for a positive best Sharpe, a grid where every configuration loses money
  scores zero degradation and passes. Before this was fixed the analyzer answered
  "ROBUST PLATEAU ... Safe to deploy" for an all-negative grid.
- **List order deciding the verdict.** Taking neighbours by list index rather than
  parameter order let one set of results be classified either way. The same five
  points, reordered, produced `FRAGILE PEAK` and `ROBUST PLATEAU` respectively.
- **One grid point "proving" robustness.** With no neighbours, degradation is zero and
  the plateau test passes vacuously.
- **The unbracketed optimum.** A monotone parameter puts the best value at whichever
  end of the range you happened to stop at, with half its neighbourhood unobserved.
- **The flat, worthless grid.** Sharpe 0.001 at every point is maximally stable. Only
  a viability hurdle catches it.
- **The tied flat grid misread as an edge case.** When every point ties for the
  maximum, a naive argmax returns index 0 — the boundary — and the ideal plateau gets
  reported as an edge optimum. Ties resolve toward the most interior point instead.
- **A degradation score mistaken for a gradient.** The reported figure is a
  dimensionless relative drop. Reading it as $\Delta S/\Delta p$ and comparing it
  across parameters with different units is meaningless.
- **Grid resolution inflating the maximum.** Halving the step size doubles the trials
  and raises the expected best Sharpe with no improvement in the strategy.

## Production Implementation Reference

- Reference code: `scripts/sensitivity_analyzer.py` (`ParameterSensitivityAnalyzer`,
  `GridPoint`, `SensitivityReport`, `RobustnessVerdict`, `SensitivityError`).
- Automated unit tests: `scripts/test_sensitivity_analyzer.py`.
