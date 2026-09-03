# Pre-Flight / Sign-off Checklist — walk-forward-hyperparameter-search-budget

## Budget

- [ ] In-sample window length is strictly positive and expressed in trading days.
- [ ] Max search budget calculated from the in-sample window duration, with the
      truncation understood (252 days → 100; 250 days → 99).
- [ ] `max_trials_per_year` reviewed as a house heuristic, not adopted as a standard.

## Grid

- [ ] Cartesian product of the parameter grid audited from axis cardinalities before
      execution — not by enumerating it.
- [ ] No empty grid and no empty axis; no axis supplied as a bare string.
- [ ] Grid pruning applied when the budget is exceeded, by **index sampling**, never by
      constant-stride slicing.
- [ ] Every parameter confirmed to take more than one distinct value in the pruned
      sample — the aliasing check that a budget assertion cannot catch.
- [ ] Sampler seed recorded, so the pruned subset is reproducible.

## Campaign

- [ ] Cumulative evaluations summed across **all** walk-forward windows, not checked
      per window.
- [ ] Cumulative budget computed on the total **distinct** data span, not the sum of
      overlapping window lengths.
- [ ] MinBTL cross-check performed; any shortfall recorded as a finding even when the
      house budget passes.
- [ ] Trials conducted outside this tool (grids abandoned by eye, prior sweeps on the
      same data) noted separately — the budgeter cannot see them.

## Testing

- [ ] Automated Testing: Run
      `python -m unittest discover -s skills/walk-forward-hyperparameter-search-budget/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
