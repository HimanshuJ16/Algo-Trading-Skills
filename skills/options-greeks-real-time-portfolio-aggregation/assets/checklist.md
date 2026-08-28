# Pre-Flight Checklist

## Inputs

- [ ] Is `multiplier` read from the contract master for **every** position, rather than defaulting to 100? (OCC-adjusted contracts change the deliverable while leaving the premium multiplier at 100; crypto options are 1 unit per contract.)
- [ ] Are `delta`, `gamma`, `theta` and `vega` per **unit of the deliverable**, not per contract?
- [ ] Is the long/short sign carried by `position_qty` ($-Q$ for short), with per-unit Greeks left as quoted?
- [ ] Are all Greeks and every `spot_price` for one book quoted in a **single currency**?
- [ ] Is $|\delta| \le 1$ on every leg? (A delta of `60` is a percent-quoted feed and a $100\times$ error.)

## Validation

- [ ] Does a non-finite Greek, quantity, spot or multiplier **raise** rather than netting into the total? (`abs(nan) > limit` is `False` — a NaN book otherwise reports as healthy.)
- [ ] Does one bad leg reject the whole book rather than producing a partial aggregate?
- [ ] Are limits validated at construction — magnitudes $> 0$, and `max_negative_theta_usd` $\le 0$?

## Aggregation

- [ ] Is Dollar Delta ($\Delta \times S$) used for any cross-underlying delta comparison?
- [ ] Is Dollar Gamma ($\Gamma S^2 \times 0.01$) used for any cross-underlying gamma comparison?
- [ ] Are raw delta units and raw gamma consumed **only** when `is_single_underlying` is true, or per-name from `by_underlying`?
- [ ] Are totals summed order-independently (`math.fsum`) so a near-limit book's status does not depend on position ordering?

## Limit audit

- [ ] Are all limits evaluated independently, with every breach reported — not just the first?
- [ ] Is the theta limit applied as a **signed floor** on decay ($\Theta < L$), never as $|\Theta| \le L$?
- [ ] Do the reported figures and the breach status agree, because both use the same rounded values?

## Interpretation

- [ ] Is daily theta read as one **calendar** day? (Models decay seven days over a five-day trading week, so Friday's reading understates the decay to Monday's open ~3×.)
- [ ] Is net vega understood as a **parallel** one-point shift across every surface in the book, not a diversified figure?
- [ ] Are the Greeks feeding this aggregate fresh? (Fast aggregation of a stale surface is a stale risk number, delivered promptly.)
- [ ] Is a breach routed to something that acts — a hedger or a circuit breaker? This engine only observes.
