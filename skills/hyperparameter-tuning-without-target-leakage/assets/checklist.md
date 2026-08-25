# Pre-Flight Checklist — Leakage-Free Hyperparameter Tuning

## Label and geometry

- [ ] Is `purge_window_samples` equal to the label horizon $h$ read off the **target definition**, not guessed or left at the default?
- [ ] Is index $i$ genuinely the $i$-th observation in **time order**? (Shuffled data produces valid-looking, meaningless splits and no error.)
- [ ] Does the sample survive the geometry — `n_samples >= outer_folds × inner_folds`, with room left after purge and embargo?
- [ ] Is the embargo actually non-zero at this sample length? (The published $\lfloor T \cdot E \rfloor$ is zero below 100 bars at 1%; this module rounds up.)

## Nesting

- [ ] Are inner folds drawn from the **outer training pool**, not from `range(n_samples)`?
- [ ] Is the **outer** training pool itself purged and embargoed against the test block — not merely "everything except the test block"?
- [ ] Is the outer test block scored exactly **once**, with no re-tuning after the score was seen?
- [ ] Does `structural_isolation_verified` read `True`?

## The callback

- [ ] Is every stateful step — scaler, encoder, imputer, feature selector — fitted on `train_indices` **only**?
- [ ] Does the callback actually use the indices it is handed rather than the full frame?
- [ ] Are the features themselves free of forward-looking construction? (Out of scope here — see `feature-engineering-without-leakage`.)

## Reading the report

- [ ] Is `out_of_sample_outer_sharpe`, not `best_inner_cv_sharpe`, the figure being quoted as an expectation?
- [ ] Does `best_inner_cv_sharpe` clear `expected_max_sharpe_under_null`? (If not, the search found the winner of a lottery.)
- [ ] If `leakage_overestimation_haircut` is zero or negative, has the callback been checked for insensitivity to its training set — rather than the zero being read as a clean result?
- [ ] Was the grid size recorded, and is it defensible against the search budget? (See `walk-forward-hyperparameter-search-budget`.)

## Reproducibility

- [ ] Do two identical runs produce identical reports? (Any unseeded randomness in the callback breaks this.)
- [ ] Are the grid, the fold counts, the purge window and the embargo percentage recorded alongside the reported score?
