# Pre-Flight / Sign-off Checklist — greeks-based-portfolio-hedging-automation

## Inputs

- [ ] Contract multiplier is supplied **per position** from the contract master, not defaulted or hard-coded to 100.
- [ ] OCC-adjusted contracts in the book have been checked against their Information Memo deliverable.
- [ ] Delta and vega are quoted per unit of the deliverable; delta values sit within $[-1, +1]$ (a `60` means the feed quoted percent).
- [ ] Position sign is carried by quantity, not by negated Greeks.
- [ ] A beta against the delta hedge instrument's underlying is supplied for every non-proxy name; defaults of 1.0 are deliberate, not accidental.
- [ ] Hedge instrument terms (price, multiplier, delta per unit, vega per unit) match the current contract specification.

## Aggregation

- [ ] Net dollar delta, beta-weighted dollar delta, and net dollar vega verified against a hand-computed book.
- [ ] Per-underlying delta breakdown reviewed for concentration hidden inside a flat total.
- [ ] Invalid positions (NaN/Inf Greek, non-positive spot or multiplier) raise rather than netting into the total.

## Hedge sizing

- [ ] Hedging triggers on the **limit**, not on the minimum rebalance size.
- [ ] Vega leg is sized before the delta leg, and the delta it injects is netted into the delta leg.
- [ ] A vega breach with no vega-carrying instrument produces an explicit warning and escalation, not an empty order list.
- [ ] Hedge quantities are truncated toward zero — no order overshoots past neutral.
- [ ] `residual_delta_usd`, `residual_vega_usd` and `is_residual_within_limits` are reviewed on every run.
- [ ] An empty order list under `is_hedging_required` has been traced to a specific warning code (`DELTA_BREACH_UNHEDGEABLE`, `HEDGE_SUPPRESSED_BELOW_MIN_SIZE`, `VEGA_*`) and is not read as "flat".

## Downstream

- [ ] Emitted orders pass through pre-trade risk controls, client-order-ID idempotency, and kill-switch coverage before routing.
- [ ] Cash-equity SELL hedges are checked for short-sale locate requirements.

## Testing

- [ ] Automated Testing: Run `python scripts/test_greeks_hedging_engine.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
