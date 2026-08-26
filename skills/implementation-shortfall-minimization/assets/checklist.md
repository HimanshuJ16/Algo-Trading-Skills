# Pre-Flight Checklist

## Benchmarks

- [ ] Is the Decision Price ($P_0$) recorded at the moment of the PM's decision — not when the algo started?
- [ ] Is the horizon price convention for marking unfilled shares fixed and recorded *before* measuring?
- [ ] If an arrival price is available, is it passed explicitly (never inferred)?

## Schedule (Almgren-Chriss)

- [ ] Are $\sigma$ and $\eta$ estimated for *this* instrument, in units consistent with $\tau$ — or is the schedule explicitly labelled as an uncalibrated urgency dial?
- [ ] Is $\kappa$ computed from $\lambda\sigma^2/\tilde\eta$ via the exact $\operatorname{arccosh}$ root, not as $\sqrt{\lambda}$?
- [ ] Is $\tilde\eta = \eta - \gamma\tau/2 > 0$?
- [ ] Does the trade list sum exactly to the parent quantity with **no negative slice**?

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
