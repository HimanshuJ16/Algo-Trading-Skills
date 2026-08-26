# Workflows for Implementation Shortfall Minimization

## A. Pre-trade — build the schedule

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

## B. Post-trade — measure what it cost

5. **Record the benchmarks.** Decision price $P_0$ at PM decision time; optionally the
   arrival price $P_a$ when the order reached the venue; the horizon price
   $P_{\text{final}}$ under a fixed, documented convention.
6. **Validate the fills before measuring.** Reject non-finite or non-positive prices,
   non-positive quantities, non-finite fees, duplicate `fill_id`, and any executed
   quantity exceeding the parent quantity. An over-fill is an order-control incident,
   not a TCA result.
7. **Decompose** with `evaluate_implementation_shortfall` ($s = +1$ buy, $-1$ sell):
   - execution cost $= s\sum_k q_k(P_k - P_0)$
   - delay cost $= s\,Q_f(P_a - P_0)$ and impact $= s\sum_k q_k(P_k - P_a)$, only when
     $P_a$ was supplied
   - opportunity cost $= s\,(Q - Q_f)(P_{\text{final}} - P_0)$
   - explicit fees, negative for maker rebates
8. **Normalise** to basis points on the intended notional $Q \times P_0$.
9. **Report**, carrying the status (`IS_EVALUATION_SUCCESS` or `IS_EVALUATION_NO_FILLS`),
   the fill ratio, and whether the delay/impact split was available. Do not present the
   combined execution cost as market impact when it was not split.

## C. Feedback loop — cautiously

10. **Compare realised shortfall against the schedule's intent**: a large opportunity
    cost with a low fill ratio argues for more urgency (higher $\lambda$); a large impact
    term on a fully filled order argues for less.
11. **Do not recalibrate $\eta$ from the measured impact term.** It contains market drift
    and news that no post-trade arithmetic can separate from the order's own footprint.
    Re-estimate impact coefficients from a dedicated impact study instead.
