---
name: backtesting-ml-models-against-transaction-costs
description: >-
  Use when an ML model flips its prediction on nearly every bar and its Sharpe depends
  on that turnover; applies per-turnover cost charges and confidence thresholding before
  the strategy is judged. Order-level cost attribution is
  transaction-cost-analysis-tca-integration.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: machine-learning, tca, transaction-costs, turnover-drag, thresholding
  brokers_frameworks: "NumPy; ML Backtesting"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Machine learning models inherently generate highly active signals (e.g., changing predictions from slightly positive to slightly negative on every bar). Without strict Transaction Cost Analysis (TCA) and confidence thresholding, these models achieve massive theoretical Sharpe ratios that instantly collapse in live trading due to **Turnover Drag**. Invoke this skill to evaluate ML model predictions against realistic slippage and commission models, filtering out low-confidence trades that do not cover their own execution costs.

## When NOT to Use

- **Order-level cost attribution.** This skill models cost as a flat bps charge per unit of turnover. To decompose realised shortfall into delay, spread, and market impact, use `transaction-cost-analysis-tca-integration`.
- **Size-dependent impact.** The flat-rate model has no ADV or participation term, so it will understate cost for orders that are large relative to available liquidity. Pair it with `liquidity-adjusted-position-sizing` and `strategy-capacity-estimation-before-scaling-capital` before scaling capital.
- **Continuous or leveraged target weights.** Positions here are discrete `{-1, 0, +1}`. A continuous-weight portfolio needs turnover computed on the weight vector — see `portfolio-construction-with-transaction-cost-awareness`.

## Prerequisites

- An array of numerical ML predictions (e.g., predicted forward returns or continuous signal strength).
- An array of actual underlying returns, **already aligned** so that element `i` is the return realised *after* `predictions[i]` was observable. The backtester cannot detect misalignment.
- Estimated transaction costs in basis points (bps) per half-turn (e.g., 5 bps for slippage + fee), calibrated against your own execution data rather than a folklore constant.

## Workflow

1. **Align predictions to forward returns**: pair `predictions[t]` with the return earned by acting on it (`t → t+1`, or `t+1 → t+2` if your execution lags the signal by a bar). Getting this wrong is the single most common way to produce a spectacular, meaningless backtest.
2. **Clean the inputs**: drop or impute NaN/Inf predictions *before* the backtest. A NaN prediction silently classified as "flat" is a hidden regime change in the strategy, not a neutral default — `MlTcaBacktester` rejects non-finite input rather than absorbing it.
3. **Set the hurdle**: the entry threshold must clear the round-trip cost. A position that is entered and later exited pays 2 units of turnover, so require `signal_threshold >= 2 * bps_cost_half_turn / 10_000` in decimal-return units. Below this line, a correct prediction is still a losing trade.
4. **Generate positions**: map predictions to `{+1, -1, 0}` against the threshold.
5. **Decide whether to apply a buy/hold spread**: if the model churns around the threshold boundary, set `exit_threshold < signal_threshold` so an open position is held until conviction genuinely decays, rather than being closed and re-entered on noise.
6. **Calculate turnover**: costs are charged on position *changes* only. Flat→long is 1 unit; long→short is 2 units; a position still open on the final bar owes its exit half-turn (`liquidate_at_end`, on by default).
7. **Apply cost drag and compute net returns**: subtract `turnover × bps_cost_half_turn / 10_000` from gross returns each period.
8. **Sweep the threshold out-of-sample**: pick `signal_threshold`/`exit_threshold` on training folds and confirm them on held-out data. Choosing the threshold that maximises net return on the same sample you report is parameter mining, not cost control — see `walk-forward-optimization-window-management`.
9. **Stress the cost assumption**: re-run at 2–3× your estimated bps. A strategy whose net edge disappears when costs double has no margin for a bad execution day.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring State Persistence**: Charging a transaction cost on every bar simply because the model predicted "Buy", even though the portfolio is *already* in a "Buy" state. Costs are only incurred on position *changes* (Turnover).
- **Zero Thresholding**: Taking trades on microscopic predictions (e.g., model predicts +0.0001% return) which guarantees a net loss when the transaction cost is 0.05%. A threshold of exactly `0` is worse than useless — it makes the long and short conditions overlap, so a neutral prediction can be classified as a short.
- **Symmetric Costs**: Forgetting that crossing the spread (market order) costs money on *both* entry and exit (half-turns).
- **The Free Exit**: Ending the backtest with an open position and never charging the closing half-turn. On a low-trade-count strategy this can hide a meaningful slice of total cost.
- **Contemporaneous Returns**: Multiplying `predictions[t]` by the return of the bar the prediction was *made on*. This is look-ahead bias, and the resulting equity curve survives any cost assumption you throw at it — see `lookahead-bias-elimination`.
- **NaN as Neutral**: Letting missing predictions default to "flat". The backtest then reports a strategy nobody can trade, and a NaN in the return series silently poisons every compounded metric.
- **Threshold Mined In-Sample**: Sweeping `signal_threshold` across the full history and reporting the best net result. The threshold is a free parameter, and cost-aware backtests overfit it as readily as any other.

## Verification

- Simulate an ML model that perfectly predicts 10% returns but flips direction every day. Apply a 6% (600 bps) per-half-turn cost. The gross return will be massive, but the net return must be decisively negative — covered by `test_perfect_but_churning_model_is_net_negative`.
- Confirm a held position is charged once, not once per bar: two consecutive identical long signals must produce 1 unit of turnover, not 2.
- Confirm a neutral (`0.0`) prediction maps to flat, never to short.
- Run `python -m unittest discover -s skills/backtesting-ml-models-against-transaction-costs/scripts` and confirm a 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `execution-cost-model-recalibration-cadence`
- `lookahead-bias-elimination`
- `execution-realistic-simulation`
- `walk-forward-optimization-window-management`
- `portfolio-construction-with-transaction-cost-awareness`
