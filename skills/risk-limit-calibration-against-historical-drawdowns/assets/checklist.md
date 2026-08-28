# Pre-Flight Checklist — Risk Limit Calibration Against Historical Drawdowns

Every threshold below is this skill's own risk policy, not a regulatory minimum. See
`references/standards.md`.

## Input data

- [ ] Are the inputs **daily fractional returns on account equity** (`0.02` = +2%), not currency P&L?
- [ ] Are they chronologically ordered and ending at the last completed session?
- [ ] Are there at least `min_observations` (default 252)? The engine refuses below 126 (~6 months) whatever is passed.
- [ ] Does the window contain at least one adverse regime, rather than only the recent calm?
- [ ] Have non-finite returns and returns `<= -1.0` been resolved **in the data**, rather than tolerated? (The engine rejects them; do not widen tolerances to get a number.)
- [ ] Is `portfolio_capital_usd` finite and strictly positive?

## Method selection

- [ ] Has the calibration method been chosen deliberately, knowing the three measure different quantities?
- [ ] For `PARAMETRIC_VAR` / `EXTREME_VALUE_THEORY`: is `horizon_days` set to the horizon actually intended, and is the IID assumption behind the horizon scaling acceptable for this strategy?
- [ ] For `EXTREME_VALUE_THEORY`: has `shape_xi` been inspected? Is it below `0.25` (no warning logged), and is the exceedance count enough to mean something?
- [ ] Is it understood that `HISTORICAL_MAX_DD` cannot exceed the worst outcome already in the sample?

## Outputs

- [ ] Are max drawdown depth, duration, Ulcer Index, VaR and ES all recorded — not depth alone?
- [ ] If `drawdown_unrecovered` is true, is the duration being reported as a **lower bound**, not as a recovery time?
- [ ] Is `floor_binding` false? If true, the limit was set by the 5% policy floor, not by the return sample.
- [ ] Is `cap_binding` false? If true, the strategy's measured risk exceeds the largest limit this engine issues — escalate rather than accepting the capped number.
- [ ] Is the stress buffer at least `1.0`, and is the chosen value written down as a policy decision?
- [ ] Is the position scalar below `1.0` where the observed drawdown exceeds the threshold?

## Governance

- [ ] Has the full `CalibratedRiskLimits` record (metrics, `tail_fit`, `limit_basis`, binding flags, `audit_notes`) been filed for review, not just the headline percentage?
- [ ] Is the recalibration cadence written down, along with the events that trigger an off-cycle recalibration?
- [ ] Are these numbers enforced by a control **outside** strategy logic (`kill-switch-and-drawdown-circuit-breakers`, `portfolio-level-stop-loss-independent-of-strategy-stops`)?
- [ ] Has anyone been stopped from describing these thresholds to an auditor or a client as regulatory minimums?
