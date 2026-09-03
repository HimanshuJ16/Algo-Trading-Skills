# Tail Risk Options Overlay — Sign-Off Checklist

## Policy
- [ ] Annual premium budget agreed and recorded as a **per-year** figure (commonly 1–3% of AUM; a policy tolerance, not a standard).
- [ ] Accepted in writing that passive OTM put buying has a documented negative long-run expected return (see `references/standards.md`), and that this program is bought for a protection floor, not for returns.
- [ ] Strike moneyness chosen (`otm_pct`) and the scenarios it does *not* cover identified.
- [ ] Roll schedule chosen: `dte_target` at purchase, `roll_dte` at exit, `holding_days = dte_target - roll_dte` > 0.
- [ ] `max_hedge_notional_pct` set; any value above 1.0 explicitly approved as a directional short rather than a hedge.

## Inputs
- [ ] Implied volatility is the **selected strike's own IV** from a calibrated surface — not ATM vol, not historical vol, not a default.
- [ ] Surface timestamp checked for staleness against the current session.
- [ ] `dividend_yield` supplied for a dividend-paying underlying.
- [ ] `contract_multiplier` read from the contract specification; adjusted-contract deliverable confirmed if a corporate action has occurred.
- [ ] Portfolio value and spot level are in the same currency.

## Sizing
- [ ] `annualized_carry_pct` ≤ `budget_pct` (this is the policy number; `carry_cost_pct` is one tranche and is not).
- [ ] `tranche_budget` equals `portfolio_value × budget_pct × holding_days / 365`.
- [ ] `binding_constraint` reviewed — `NOTIONAL_CAP` investigated, not silently accepted.
- [ ] `notional_coverage_ratio` ≤ 1.0 unless a directional short was explicitly approved.
- [ ] `greeks["delta"]` is small and negative for an OTM put (order -0.05 to -0.15 at 15% OTM); a near -1.0 delta indicates a pricing error, not a strong hedge.

## Stress review
- [ ] Stress table read **net of premium** (`net_payout`, `net_coverage_ratio`), not gross.
- [ ] Negative coverage at shallow drops acknowledged as expected behaviour.
- [ ] Understood that terminal-intrinsic payoffs are a floor, not a mark-to-market forecast.

## Operations
- [ ] Roll date calendared at `roll_dte`; plan re-run with fresh spot, IV and portfolio value at each roll.
- [ ] Understood that a constant budget buys the least protection right after a volatility spike; escalation path defined if protection falls below the required floor.
- [ ] Non-finite inputs are allowed to raise; no `except ValueError` that substitutes a default volatility.

## Verification
- [ ] `python -m unittest discover -s skills/tail-risk-hedging-with-options/scripts -v` — 38 tests pass.
- [ ] `python tools/validate_skills.py` passes.
