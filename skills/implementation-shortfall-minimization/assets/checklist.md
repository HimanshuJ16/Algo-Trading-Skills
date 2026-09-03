# Pre-Flight Checklist

## Benchmarks

- [ ] Is the Decision Price ($P_0$) recorded at the moment of the PM's decision — not when the algo started?
- [ ] Is the horizon price convention for marking unfilled shares fixed and recorded *before* measuring?
- [ ] Is the arrival price captured as the **median top-of-book mid over the one-second window at submission**, rather than a single tick?
- [ ] Is that snapshot stored immutably with the order, and never overwritten by a later "current mid"?
- [ ] Are crossed quotes rejected upstream rather than averaged into the benchmark?
- [ ] If an arrival price is available, is it passed explicitly (never inferred)?

## Schedule (Almgren-Chriss)

- [ ] Are $\sigma$ and $\eta$ estimated for *this* instrument, in units consistent with $\tau$ — or is the schedule explicitly labelled as an uncalibrated urgency dial?
- [ ] Is $\kappa$ computed from $\lambda\sigma^2/\tilde\eta$ via the exact $\operatorname{arccosh}$ root, not as $\sqrt{\lambda}$?
- [ ] Is $\tilde\eta = \eta - \gamma\tau/2 > 0$?
- [ ] Does the trade list sum exactly to the parent quantity with **no negative slice**?
- [ ] Is urgency matched to the alpha-decay horizon and the instrument's liquidity tier?
- [ ] Is patience being bought by lowering $\lambda$ rather than by lengthening the horizon (the half-life $1/\kappa$ is independent of $T$)?
- [ ] Does a long-horizon schedule stay on the exact curve — no overflow, no NaN, no 100%-in-interval-0 collapse?

## Cost forecast

- [ ] Is $E(x)$, $V(x)$ and the objective recorded for the schedule before it is routed?
- [ ] Do the `ImpactParameters` use the **same** $\tau$ and time unit as the schedule?
- [ ] Is the realised-vs-forecast comparison written in units of `stdev`, never `variance`?

## Execution

- [ ] Is child-order timing and sizing randomised within each interval so the pattern is not exploitable?
- [ ] Is the catch-up / give-up policy for rejected or partially filled children decided *before* trading, and recorded with the order?
- [ ] Is each child placement idempotent and inside the rate-limit budget?

## Fills and validation

- [ ] Are all fill prices finite and positive, all quantities positive, all fees finite?
- [ ] Are `fill_id` values unique, so a replayed fill cannot be double-counted?
- [ ] Does executed quantity stay within the parent quantity (an over-fill halts the measurement)?

## Decomposition

- [ ] Do execution cost, opportunity cost and fees sum to the reported total?
- [ ] Is opportunity cost included for **unexecuted** quantity?
- [ ] Is the executed-leg cost labelled "market impact" **only** when an arrival price actually split it?
- [ ] Are costs signed positive-is-loss for both buys and sells?
- [ ] Are basis points quoted on the intended notional $Q \times P_0$, not the executed notional?

## Reporting

- [ ] Is `IS_EVALUATION_NO_FILLS` handled distinctly, with VWAP reported as absent rather than as $P_0$?
- [ ] Is the impact term presented as attribution — never fed back as an $\eta$ estimate?
