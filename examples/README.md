# Runnable Examples & Cookbook Walkthroughs

This directory contains complete, runnable Python scripts demonstrating how to chain multiple skills from `Algo-Trading-Skills` together into cohesive institutional quant workflows.

## 📚 Included Walkthroughs

| Walkthrough | Chained Skills | Purpose |
|---|---|---|
| [`01_resilient_order_execution_and_circuit_breaker.py`](01_resilient_order_execution_and_circuit_breaker.py) | `order-placement-idempotency` + `kill-switch-and-drawdown-circuit-breakers` + `token-lifecycle-live-probing` | Simulates an live trading order loop subject to network timeouts, token expiry, and max drawdown circuit breaker triggers. |
| [`02_lookahead_free_backtest_with_slippage.py`](02_lookahead_free_backtest_with_slippage.py) | `lookahead-bias-elimination` + `realistic-slippage-fee-latency-simulation` + `standardized-tearsheet-generation` | Builds a point-in-time backtester with execution latency, quadratic market impact slippage, and institutional performance tear-sheets. |
| [`03_cross_strategy_risk_parity_allocation.py`](03_cross_strategy_risk_parity_allocation.py) | `cross-strategy-correlation-monitoring` + `risk-parity-allocation` + `strategy-lifecycle-retirement-criteria` | Monitors rolling strategy correlations, rebalances portfolio weights using risk parity, and automatically triggers strategy retirement when performance decays. |

## 🚀 Running the Examples

Each script is standalone and runnable with standard Python 3.8+:

```bash
python examples/01_resilient_order_execution_and_circuit_breaker.py
python examples/02_lookahead_free_backtest_with_slippage.py
python examples/03_cross_strategy_risk_parity_allocation.py
```
