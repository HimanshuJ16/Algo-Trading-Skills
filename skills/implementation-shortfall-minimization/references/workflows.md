# Workflows for Implementation Shortfall Minimization

## A. Capture the benchmark

0. **At the instant of submission, snapshot the arrival price** with
   `median_mid_arrival_price`: the median top-of-book mid over the one-second window.
   - A single tick is one draw from the quote-flicker distribution; the median is robust
     to a stale or wide print that would relocate a single-tick benchmark entirely.
   - A crossed quote raises rather than being averaged in. Resolve it upstream.
   - **Store it immutably with the order.** It is the benchmark for the whole parent, and
     must never be overwritten by a later "current mid" — that makes the benchmark chase
     the price the order is itself moving.
   - Keep it distinct from the decision price $P_0$; the gap between them is the delay cost.

## B. Pre-trade — build the schedule

1. **Estimate the model inputs for the instrument.** Volatility $\sigma$ per
   $\sqrt{\text{interval}}$ and temporary impact $\eta$ must come from the name being
   traded, in units consistent with each other and with $\tau$. The library defaults
   ($\sigma = 1$, $\eta = 1$, $\gamma = 0$, $\tau = 1$) are dimensionless placeholders
   and describe no instrument.
2. **Compute $\kappa$** with `almgren_chriss_kappa`:
   $\tilde\eta = \eta - \gamma\tau/2$, $\tilde\kappa^2 = \lambda\sigma^2/\tilde\eta$,
   $\kappa = \operatorname{arccosh}(1 + \tilde\kappa^2\tau^2/2)/\tau$.
   - $\tilde\eta \le 0$ raises — the cost functional is degenerate; shorten $\tau$ or
     re-estimate the coefficients rather than clamping.
   - $\lambda = 0$ gives $\kappa = 0$ and an exact TWAP. $\lambda < 0$ raises.
3. **Generate the trade list** with `calculate_almgren_chriss_trajectory`. Slices are
   differences of a monotone-rounded holdings path, so each is $\ge 0$ and they sum to
   $Q$ exactly.
4. **Sanity-check the shape before routing**: front-loaded for high $\lambda$, flat for
   $\lambda \to 0$, no negative slice, sum equals the parent quantity.
   - **Decision point — patience comes from $\lambda$, not from $T$.** The half-life
     $1/\kappa$ does not depend on the horizon, so adding intervals at fixed urgency leaves
     the leading intervals untouched and appends near-empty tail intervals. If the schedule
     is too aggressive, lower $\lambda$.
5. **Forecast what the schedule should cost** with
   `forecast_shortfall(schedule, ImpactParameters(sigma, eta, gamma, epsilon, tau), lambda)`.
   Record $E$, `stdev` and the objective alongside the order; they are the yardstick step 11
   reads the realised shortfall against. Use the *same* $\tau$ the schedule was built with.

## C. Execution — work the schedule

6. **Route the child orders** on the schedule, randomising timing and sizing slightly within
   each interval so the pattern is not exploitable. Slicing multiplies placements, so each
   child needs its own idempotency key and rate-limit budget.
7. **Handle deviations under a pre-decided policy.** On a rejection or partial fill, either
   **catch up** (redistribute the remainder across the remaining intervals — more impact) or
   **give up** (accept an incomplete execution — more opportunity cost). Decide before
   trading, and never blindly resubmit the same child size. The choice shows up in the
   post-trade decomposition as impact versus opportunity cost, so record which was used.
8. **Treat the schedule as a plan, not a contract.** Re-evaluate against live depth when the
   book thins or the price gaps.

## D. Post-trade — measure what it cost

9. **Record the benchmarks.** Decision price $P_0$ at PM decision time; optionally the
   arrival price $P_a$ when the order reached the venue; the horizon price
   $P_{\text{final}}$ under a fixed, documented convention.
10. **Validate the fills before measuring.** Reject non-finite or non-positive prices,
   non-positive quantities, non-finite fees, duplicate `fill_id`, and any executed
   quantity exceeding the parent quantity. An over-fill is an order-control incident,
   not a TCA result.
11. **Decompose** with `evaluate_implementation_shortfall` ($s = +1$ buy, $-1$ sell):
   - execution cost $= s\sum_k q_k(P_k - P_0)$
   - delay cost $= s\,Q_f(P_a - P_0)$ and impact $= s\sum_k q_k(P_k - P_a)$, only when
     $P_a$ was supplied
   - opportunity cost $= s\,(Q - Q_f)(P_{\text{final}} - P_0)$
   - explicit fees, negative for maker rebates
12. **Normalise** to basis points on the intended notional $Q \times P_0$.
13. **Report**, carrying the status (`IS_EVALUATION_SUCCESS` or `IS_EVALUATION_NO_FILLS`),
   the fill ratio, and whether the delay/impact split was available. Do not present the
   combined execution cost as market impact when it was not split.

## E. Feedback loop — cautiously

14. **Compare realised shortfall against the forecast**: a large opportunity
    cost with a low fill ratio argues for more urgency (higher $\lambda$); a large impact
    term on a fully filled order argues for less. Measure the gap in units of the
    forecast's `stdev`, **never** its `variance` — $V$ is in currency squared, so a
    threshold written against it compares incommensurable units and never fires. A
    persistent excess over $E + k\,\text{stdev}$ means the $\sigma$/$\eta$ assumptions
    feeding $\kappa$ are miscalibrated, not that the algorithm is broken.
15. **Do not recalibrate $\eta$ from the measured impact term.** It contains market drift
    and news that no post-trade arithmetic can separate from the order's own footprint.
    Re-estimate impact coefficients from a dedicated impact study instead.
