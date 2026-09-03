---
name: backtest-parameter-sensitivity-analysis
description: >-
  Use after optimising strategy parameters, to perturb them across a grid and measure
  how fast Sharpe decays; separates a fragile overfit peak from a genuine plateau. It
  does not deflate the selected Sharpe for trial count.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, parameter-sensitivity, overfitting-detection, grid-search, robustness, sharpe-surface
  brokers_frameworks: "Parameter Sensitivity Analyzer; Python"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill after optimizing strategy parameters. A strategy whose Sharpe jumps from 0.5 to 3.0 with a $\pm 1\%$ parameter tweak is overfit. This skill systematically perturbs parameters across a grid and measures the gradient of Sharpe ratio to distinguish fragile peaks from robust plateaus.

It is a **screen, not a certificate**. A plateau verdict says the optimum survives a one-step parameter perturbation. It says nothing about out-of-sample performance, regime stability, or whether the Sharpe is real.

## When NOT to Use

- **Not a Sharpe deflation.** `best_sharpe` is the maximum over N grid points and is upward-biased by selection even when true skill is zero — it is a maximum of N noisy estimates. Correcting it needs the trial count and the return distribution's higher moments. `total_grid_points` is reported so you can do that downstream; see `factor-research-multiple-testing-correction` and the Deflated Sharpe Ratio reference in `references/standards.md`.
- **Not a multi-parameter analysis.** The implementation sweeps one parameter at a time and cannot see interaction effects. A pair of parameters can each look like a plateau in isolation while the joint surface is a knife edge. Sweep a full grid and inspect the surface for that.
- **Not a substitute for out-of-sample testing.** Plateau stability is measured entirely in-sample. Use `walk-forward-validation-setup` and `multi-year-regime-coverage-requirement` for the out-of-sample question.
- **Not meaningful on a near-zero Sharpe.** A flat grid at Sharpe 0.01 is perfectly stable and worthless. Set `min_viable_sharpe` to your actual deployment hurdle; the 0.0 default only rules out strategies that lose money everywhere.
- **Not usable with a noisy backtest you have not characterised.** If re-running the same configuration moves Sharpe by 20%, a 15% degradation threshold measures your simulator's noise, not the strategy's fragility.

## Prerequisites

- Strategy with tunable parameters (e.g., lookback window, entry threshold).
- Backtest engine that accepts parameter overrides and returns performance metrics.
- A deterministic backtest, or a known run-to-run Sharpe dispersion to calibrate the degradation threshold against (see `backtest-determinism-and-reproducibility`).
- A parameter range wide enough to **bracket** the optimum on both sides.

## Workflow

1. **Define Parameter Grid**: Specify parameter ranges and step sizes. Range matters more than resolution: if the best value lands on the first or last point, the optimum is not bracketed and the analyzer returns `EDGE_OPTIMUM` rather than a verdict.
2. **Run Grid Sweep**: Execute backtest for each parameter combination. A non-finite Sharpe (zero return volatility, empty trade log) is rejected rather than swept in — every comparison against NaN is False, so an unguarded NaN silently produces a "robust" result.
3. **Compute Neighbourhood Degradation**: Order the grid **by parameter value**, locate the best point, and measure the relative Sharpe drop to its worst immediate neighbour:
   $$\text{degradation} = \frac{S_{\text{best}} - \min(S_{\text{left}}, S_{\text{right}})}{S_{\text{best}}}$$
   This is a dimensionless ratio, not $\Delta\text{Sharpe}/\Delta\text{Param}$. A raw derivative is not thresholdable across parameters, because 0.2 Sharpe per lookback-day and 0.2 Sharpe per threshold-unit are not comparable quantities.
4. **Classify**: The verdict ladder runs viability → coverage → bracketing → degradation, and stops at the first failure:
   - `NOT_VIABLE` — best Sharpe at or below `min_viable_sharpe`. A flat grid of losses is stable, not robust.
   - `INSUFFICIENT_GRID` — fewer than 3 points; a plateau needs a neighbour on each side.
   - `EDGE_OPTIMUM` — best point is at the boundary, so half its neighbourhood was never observed.
   - `FRAGILE_PEAK` / `ROBUST_PLATEAU` — degradation above or within `max_neighborhood_degradation_pct`.
5. **Deflate Before Believing the Number**: Carry `total_grid_points` forward as the trial count and correct `best_sharpe` for selection bias before treating it as an expectation.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single-Parameter Analysis Only**: Ignoring interaction effects between correlated parameters. This tool is itself single-parameter — see "When NOT to Use".
- **Too Fine Grid**: Overfitting the grid search itself. Every extra grid point is another trial inflating the maximum.
- **Unordered Grid**: Neighbours must be taken in parameter order. Indexing into the caller's list order lets the same set of results be classified either way depending on how it was assembled — this analyzer sorts by parameter value to prevent it.
- **Unbracketed Optimum**: A monotonically improving parameter puts the best value at the edge of whatever range you happened to sweep. That is a statement about your grid, not about a plateau. Widen the range.
- **A Stable Loser Reported as Robust**: If degradation is only computed when the best Sharpe is positive, a grid where every configuration loses money scores zero degradation and passes the plateau test.
- **Treating a Plateau as Permission to Deploy**: The verdict covers one parameter, in-sample, at one grid resolution. It is one input to a deployment decision, not the decision.
- **Threshold Below Simulator Noise**: A degradation threshold tighter than your backtest's own run-to-run dispersion flags noise as fragility.

## Verification

- Run a monotonically increasing Sharpe curve and confirm the verdict is `EDGE_OPTIMUM`, not a plateau.
- Run a grid where every Sharpe is negative and confirm the verdict is `NOT_VIABLE`.
- Shuffle a grid's list order and confirm the verdict, degradation and best parameters are unchanged.
- Assert the hand-computed boundary: best 4.0 against worst neighbour 3.0 is exactly $(4-3)/4 = 0.25$ degradation, which is robust at a 0.25 threshold and fragile at anything tighter.
- Confirm a single grid point returns `INSUFFICIENT_GRID` rather than a robustness verdict.
- Run `python -m unittest discover -s skills/backtest-parameter-sensitivity-analysis/scripts` — 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `multi-year-regime-coverage-requirement`
- `factor-research-multiple-testing-correction`
- `monte-carlo-strategy-robustness-testing`
- `backtest-determinism-and-reproducibility`
---
