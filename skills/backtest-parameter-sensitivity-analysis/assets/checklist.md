# Pre-Flight / Sign-off Checklist — backtest-parameter-sensitivity-analysis

Use this before considering the skill's implementation complete.

## Grid Design

- [ ] **Optimum is bracketed.** Best value is not the first or last point; `best_at_grid_edge` is False. If it is at the edge, the range was widened and the sweep re-run.
- [ ] **Range chosen before resolution.** Grid step is no finer than the parameter change the strategy can actually distinguish.
- [ ] **Trial count recorded**, including sweeps that were run and discarded — not just the one being reported.
- [ ] **Parameter values unique and finite.** No duplicated grid point.

## Threshold Calibration

- [ ] **Backtest noise measured.** One configuration re-run several times; the degradation threshold sits above the observed run-to-run Sharpe dispersion, or the backtest is deterministic.
- [ ] **`min_viable_sharpe` set to a real deployment hurdle**, not left at 0.0.
- [ ] **`max_neighborhood_degradation_pct` choice documented**, with the reasoning — the 0.15 default has no published basis.

## Analysis

- [ ] **Grid ordered by parameter value** before neighbours are taken (the analyzer does this; confirm nothing upstream depends on list order).
- [ ] **Verdict consumed via `report.verdict`**, not by substring-matching the message.
- [ ] **Non-finite Sharpes resolved at source**, not filtered away — a NaN Sharpe usually means zero return volatility or an empty trade log.
- [ ] **`EDGE_OPTIMUM`, `NOT_VIABLE` and `INSUFFICIENT_GRID` treated as unresolved**, not as soft passes.

## Interpreting the Result

- [ ] **`best_sharpe` deflated for selection bias** using `total_grid_points` before being quoted as an expectation.
- [ ] **Every parameter screened**, and the joint surface inspected for interaction effects that one-dimensional passes cannot see.
- [ ] **Out-of-sample validation run separately.** Plateau stability is entirely in-sample.
- [ ] **Plateau verdict not treated as deployment approval.** It is one input among walk-forward results, regime coverage and risk limits.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtest-parameter-sensitivity-analysis/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Parameter, range, trial count and thresholds used: ___________________________
