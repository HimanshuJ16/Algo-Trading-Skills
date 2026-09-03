---
name: walk-forward-hyperparameter-search-budget
description: >-
  Use when walk-forward optimisation would otherwise run tens of thousands of parameter
  combinations per window; bounds and audits the cumulative trial count that drives
  selection bias. It never sees a performance number.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, hyperparameter-budget, walk-forward, overfitting-prevention, pbo, search-space-bounding
  brokers_frameworks: "Hyperparameter Search Budgeter; Python"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill during walk-forward validation setup. Running unconstrained hyperparameter searches (e.g. 50,000 parameter combinations per in-sample window) guarantees finding parameters that perform artificially well in-sample by pure chance, leading to severe out-of-sample degradation. Bounding the search budget $N_{\text{evals}} \le N_{\text{max}}$ caps the number of trials the final parameter choice is selected from, which is the quantity that drives selection bias.

It is a **trial counter and grid sampler, not a statistical test**. It makes the size of your search explicit and auditable. It does not tell you whether the surviving strategy is real.

## When NOT to Use

- **Not a PBO calculation.** The Probability of Backtest Overfitting is estimated by combinatorially symmetric cross-validation over the *performance* of each trial. This skill never sees a performance number — it only counts and bounds configurations. Budget compliance is not a PBO estimate and must not be reported as one.
- **Not a Sharpe deflation.** The best in-sample Sharpe over $N$ trials is upward-biased even at zero true skill. Correcting it requires the return distribution's higher moments; see `factor-research-multiple-testing-correction` and the Deflated Sharpe Ratio reference in `references/standards.md`.
- **Not an effective-independent-trials estimator.** The MinBTL diagnostic assumes *independent* trials. Adjacent grid points are highly correlated, so passing a raw grid count in returns a conservative (long) data requirement. Treat it as an order-of-magnitude signal, not a threshold to tune against.
- **Not a defence against sequential researcher bias.** The budget bounds one automated sweep. It cannot see the twenty grids you discarded by eye before running this one; those trials count too, and nothing here records them.
- **Not applicable to a single pre-registered configuration.** If the parameters are fixed in advance and never searched, $N = 1$ and there is no selection bias to bound.

## Prerequisites

- Parameter space dimensions $D$ (number of tunable parameters) and grid sizes $K_i$, each with at least one candidate value.
- Length of in-sample training window $T_{\text{in}}$ (trading days), strictly positive.
- The total distinct trading-day span of the walk-forward campaign, for the cumulative audit. This is *not* the sum of the in-sample window lengths — overlapping windows reuse the same data.

## Workflow

1. **Calculate Raw Parameter Combination Space**:
   $$N_{\text{raw}} = \prod_{i=1}^D K_i$$
   Reject an empty grid or an empty axis rather than computing a product of zero. A zero-size space compares as "within budget" and yields a compliant-looking report for a search that evaluates nothing.

2. **Compute Max Recommended Search Budget**:
   $$N_{\text{max}} = \mathrm{clamp}\!\left(\left\lfloor \frac{T_{\text{in}}}{252} \times R \right\rfloor,\; 10,\; 500\right)$$
   where $R$ is `max_trials_per_year` (default 100). The division truncates: $T_{\text{in}} = 252$ gives exactly 100, while $T_{\text{in}} = 250$ gives 99. This is a **house heuristic with no published basis** — see `references/standards.md` before treating either the rate or the clamp as authoritative.

3. **Prune Search Space or Sample Grid**:
   If $N_{\text{raw}} > N_{\text{max}}$, draw a bounded subset to enforce $N_{\text{evals}} \le N_{\text{max}}$. **Sample the flat index space; do not take every $k$-th combination.** A constant stride aliases against the grid's mixed-radix layout — with `itertools.product` ordering the last parameter varies fastest, so a stride sharing a factor with its cardinality freezes that parameter at one value for the entire sweep. Sample by index rather than enumerating and slicing, so a 50,000-point grid is never materialised in order to be reduced to 100.

4. **Audit Cumulative Evaluations Across Windows**:
   Sum $N_{\text{evals}}$ over every walk-forward window and compare it against the budget implied by the **total distinct data span**, not against the per-window limit. Per-window compliance is guaranteed by step 3 and therefore proves nothing: ten windows each capped at 100 is a 1,000-trial selection process, and the final parameter choice inherits the bias of all 1,000.

5. **Read the Budget Against MinBTL**:
   Compare the campaign's trial count to the Minimum Backtest Length it implies. If the required span exceeds the data you have, the house budget passed and the strategy is still under-evidenced — the heuristic is materially more permissive than the literature. Resolve the disagreement in favour of MinBTL, or record why you did not.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Searching 10,000 Combinations on 1-Year Data**: Over-optimizing 250 bars of data with thousands of parameter trials.
- **Ignoring Cumulative Search Budget**: Counting trials per window separately without auditing the total across all 10 walk-forward windows. Each window is within budget by construction, so a per-window check can never fail.
- **Summing Window Lengths as the Campaign Span**: Ten overlapping 252-day windows span far less than 2,520 distinct days. Counting window-days inflates the cumulative allowance by the overlap factor.
- **Constant-Stride Grid Pruning**: Taking every $k$-th combination looks uniform and is not. It silently freezes whichever parameters have a cardinality sharing a factor with $k$, producing a budget-compliant sweep that never varied them. Sample indices instead.
- **Enumerating the Grid in Order to Shrink It**: Building the full Cartesian product before slicing defeats the purpose — the memory blow-up happens on the grid you were trying to avoid searching.
- **Treating a Non-Positive Window as Valid**: A zero or negative $T_{\text{in}}$ clamped to the floor budget reports a healthy allowance for a window that does not exist. Reject it.
- **Reading Budget Compliance as Evidence of Robustness**: A passing budget says the search was small. It says nothing about whether the selected parameters generalise.
- **Non-Reproducible Pruning**: An unseeded sampler draws a different subset on every run, so a result cannot be reproduced or a regression bisected. Seed it.

## Verification

- Submit a parameter grid with $N_{\text{raw}} = 500$ on a 1-year in-sample window ($T_{\text{in}} = 252$), and confirm the budget is $N_{\text{max}} = 100$, pruning is applied, and exactly 100 combinations are returned.
- Assert the truncation boundary by hand: $T_{\text{in}} = 252$ yields exactly 100, $T_{\text{in}} = 250$ yields 99, $T_{\text{in}} = 1260$ yields the 500 cap, $T_{\text{in}} = 25$ yields the floor of 10.
- Prune a $20 \times 20 \times 10$ grid to 100 points and confirm **every** parameter takes more than one distinct value in the sample. Constant-stride pruning returns 1 of 10 values on the last axis and passes every budget assertion regardless.
- Confirm two runs with the same seed return identical combinations, and that a different seed returns a different subset.
- Confirm a grid exactly at budget is not pruned, and one combination over budget is.
- Confirm an empty grid, an empty axis, and a non-positive $T_{\text{in}}$ each raise rather than returning a compliant report.
- Confirm the MinBTL diagnostic reproduces its published anchor: 45 independent trials require 5.00 years at a target Sharpe of 1.0.
- Audit ten windows of 100 evaluations over a 1,260-day span and confirm the cumulative overrun is flagged even though every window passed individually.
- Run `python -m unittest discover -s skills/walk-forward-hyperparameter-search-budget/scripts` — 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `walk-forward-optimization-window-management`
- `hyperparameter-tuning-without-target-leakage`
- `backtest-parameter-sensitivity-analysis`
- `factor-research-multiple-testing-correction`
- `monte-carlo-strategy-robustness-testing`
- `backtest-determinism-and-reproducibility`
---
