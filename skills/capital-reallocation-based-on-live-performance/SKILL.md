---
name: capital-reallocation-based-on-live-performance
description: Fractional-Kelly capital allocation engine that re-weights funding across
  multiple active strategies from their trailing live trade statistics, bounded by
  per-strategy capacity ceilings and total fund capital.
domain: Portfolio Management
subdomain: Capital Allocation
tags:
- capital-allocation
- dynamic-weighting
- kelly-criterion
- position-sizing
- portfolio
brokers_frameworks:
- Generic Portfolio Management
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing a multi-strategy fund or portfolio where capital is limited and needs to be distributed among competing algorithms. Instead of static, equal-weight allocations, the engine converts each strategy's trailing live trade statistics (win rate, average win, average loss) into a fractional Kelly exposure target, then resolves those targets against each strategy's capacity ceiling and the size of the fund.

The engine sizes each strategy from its **own** edge. It is a position-sizing rule, not a ranking rule: two strategies with identical edge get identical capital regardless of how many other strategies exist, and a portfolio with weak aggregate edge deliberately leaves capital in cash.

## When NOT to Use

- **Strongly correlated strategies.** The engine sizes each strategy independently and sums the results. The true multi-asset Kelly solution is $f^* = \Sigma^{-1}\mu$, which accounts for covariance. Summing independent Kelly fractions *overstates* safe gross exposure when strategies are positively correlated — two strategies running the same trade are one bet, not two. Use a lower `kelly_fraction` plus an external gross-exposure cap, and see `cross-strategy-correlation-monitoring`.
- **Thin trade samples.** Kelly is exquisitely sensitive to estimation error in the win rate. A strategy with 20 live trades has a win-rate standard error near 10 percentage points, which swings the Kelly weight far more than the underlying edge does. Do not reallocate on a sample too small to distinguish edge from variance.
- **Non-binary payoff profiles.** The implemented formula is the discrete binary-bet Kelly. Strategies whose returns are heavily skewed or fat-tailed (short options, carry trades with rare large losses) are poorly summarised by a single win rate and average win/loss pair.
- **As a risk control.** This engine allocates capital; it does not enforce limits. Drawdown circuit breakers and kill switches must sit downstream and independently — see `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Multiple independent trading strategies reporting closed-trade PnL.
- A centralized fund/portfolio controller capable of adjusting per-strategy buying power.
- A trailing trade sample per strategy large enough to estimate win rate, average win and average loss with meaningful precision.
- A per-strategy capacity estimate (`max_capacity`) derived from liquidity, not from performance — see `strategy-capacity-estimation-before-scaling-capital`.

## Workflow

1. **Aggregate trailing statistics**: At the reallocation boundary (EOD or weekly), compute each strategy's win rate and average win/loss magnitudes over a fixed trailing trade window. Use *closed* trades only; including open positions marks a strategy on unrealised PnL that may reverse.
2. **Compute fractional Kelly targets**: The engine computes $f^* = W - (1-W)/R$ per strategy, where $R$ is the reward/risk ratio, floors it at zero, and scales it by `kelly_fraction`. A strategy with no measurable edge targets zero capital rather than a small position.
3. **Resolve against constraints**: The engine applies two ceilings per strategy — its capacity limit and its own fractional Kelly target — and one fund-level constraint: total targets never exceed total fund capital. If gross Kelly demand exceeds the fund, all targets scale down proportionally; the engine never levers.
4. **Read the cash reserve, don't fight it**: If gross Kelly demand is below 1.0, the undeployed remainder is intentional. Do not redistribute it to fill the fund — forcing it into the remaining strategies bets them above their own Kelly optimum, which is precisely the over-betting the fractional multiplier exists to prevent.
5. **Enact deltas as risk-limit changes**: `delta_capital` instructs the OMS to change a strategy's buying power. A negative delta means stop granting new risk and let existing positions exit naturally; it is not a liquidation order.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Normalising the Kelly fraction away**: If you convert Kelly weights into portfolio weights by dividing each by their sum, the fractional multiplier cancels out algebraically and Quarter-Kelly allocates exactly as much as Full-Kelly. The safeguard silently stops existing while still appearing in the config. Verify it bites: halving `kelly_fraction` must halve deployed capital.
- **Chasing noise**: Reallocating too frequently (intraday, or on tick-by-tick PnL) causes whipsawing — capital arrives at a strategy exactly as it peaks and mean-reverts.
- **Full Kelly recklessness**: Full Kelly maximises long-run growth *given known parameters*; with estimated parameters it reliably produces severe drawdowns. Under the standard quadratic growth approximation, betting $c\times$ Kelly retains $2c - c^2$ of the optimal growth rate — Half-Kelly keeps ~75% of the growth for half the exposure, and growth turns negative beyond $2\times$ Kelly. The engine rejects `kelly_fraction > 1.0`.
- **Ignoring capacity**: Allocating \$100M to a micro-cap strategy because its Kelly weight is high, when it cannot absorb more than \$5M without destroying its edge through market impact. Capacity must be an independent input, never inferred from returns.
- **Order-dependent allocation**: If the capping pass mutates its running weight total while iterating the strategy set, the same inputs produce different allocations depending on dictionary order, and capital can be silently stranded while other strategies still have headroom. Allocation must be a pure function of the inputs.
- **Minimum allocation floors**: A "no strategy drops below 5%" floor is incompatible with edge-based sizing — it forces capital into a strategy the model has just judged to have no edge. Use a cash reserve instead of a floor.
- **Treating an unbeaten strategy as infinite edge**: A strategy with zero average loss has an undefined reward/risk ratio. That is an unestimable edge, not an unbounded one; the engine allocates zero and logs a warning.

## Verification

- Run `python -m unittest discover -s skills/capital-reallocation-based-on-live-performance/scripts`.
- Confirm `kelly_fraction` governs exposure: run the same portfolio at 1.0, 0.5 and 0.25 and check deployed capital scales proportionally.
- Confirm order independence: shuffle the strategy dict and check the allocation is unchanged.
- Confirm no target exceeds `min(max_capacity, fund × fractional Kelly)`, and that the sum of targets never exceeds total fund capital.
- Simulate two strategies, one with steady wins and one on a losing streak, and verify capital re-weights toward the winner while remaining bounded by capacity.

## Related Skills

- `multi-strategy-capital-allocation-limits`
- `incremental-capital-deployment-for-new-strategies`
- `strategy-capacity-estimation-before-scaling-capital`
- `cross-strategy-correlation-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
