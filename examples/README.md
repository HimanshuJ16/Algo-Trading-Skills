# Runnable Examples & Cookbook Walkthroughs

Three complete, runnable scripts that chain skills from `Algo-Trading-Skills`
into one workflow each. They **import the real helper modules** out of
`skills/<slug>/scripts/` — the walkthroughs are wiring and narration, and the
behaviour on show is the skills' own, not a simplified restatement of it.

## Included walkthroughs

| Walkthrough | Chained skills | Purpose |
|---|---|---|
| [`01_resilient_order_execution_and_circuit_breaker.py`](01_resilient_order_execution_and_circuit_breaker.py) | `token-lifecycle-live-probing` + `order-placement-idempotency` + `kill-switch-and-drawdown-circuit-breakers` | Simulates a live order loop through a lost broker response: the token is probed before anything is sent, the timed-out order is reconciled against the broker's book instead of re-sent, and a drawdown breach latches the circuit breaker, which then vetoes the next order. |
| [`02_lookahead_free_backtest_with_slippage.py`](02_lookahead_free_backtest_with_slippage.py) | `lookahead-bias-elimination` + `execution-realistic-simulation` + `backtest-reporting-standardized-tearsheet` | Builds a point-in-time backtest whose fills land on the next bar's open, calibrates the lookahead auditor before trusting its clean verdict, prices each fill by crossing the half-spread and paying square-root market impact plus the statutory fee stack, and reports one standardized tearsheet. |
| [`03_cross_strategy_risk_parity_allocation.py`](03_cross_strategy_risk_parity_allocation.py) | `cross-strategy-correlation-monitoring` + `risk-parity-allocation-across-strategies` + `strategy-lifecycle-retirement-criteria` | Monitors pairwise strategy correlations and the diversification ratio, feeds that same covariance into an equal-risk-contribution allocation, adjudicates each strategy against a pre-declared retirement rule, and reallocates what survives. |

## Running the examples

Standard library plus `numpy` and `pandas` (see `requirements-dev.txt`), on
Python 3.10+. Run them from the repository root — each script derives the repo
root from its own `__file__` and puts the relevant `skills/*/scripts`
directories on `sys.path`:

```bash
python examples/01_resilient_order_execution_and_circuit_breaker.py
python examples/02_lookahead_free_backtest_with_slippage.py
python examples/03_cross_strategy_risk_parity_allocation.py
```

Each finishes in seconds, exits 0, and leaves nothing behind: example 01 needs a
durable SQLite ledger, so it writes one into a temporary directory that is
removed on the way out.

All randomness is seeded (`random.seed(42)` / `numpy.random.default_rng(42)`),
so the printed numbers are the same on every run. Those numbers describe the
machinery on synthetic data; none of them is a claim about a strategy.
