# Pre-Flight Checklist — New Strategy Onboarding

## Who attested to what

- [ ] Did each attestation come from the **control owner** rather than the strategy
      author? (Research for the backtest metrics, operations for the paper run, risk
      for the kill switch, model risk for the card, compliance for the sign-off.)
- [ ] Is the evidence behind each flag retrievable — logs, the model card, the dated
      sign-off — or only the flag itself?

## Gate 1 — Backtest robustness

- [ ] Is the walk-forward score $\ge$ the configured floor, and does it use the **same
      convention** the threshold was set for?
- [ ] Is regime coverage $\ge$ the configured floor, counted by an actual regime
      classification rather than by eye?
- [ ] Is the backtest Sharpe being used as a **floor against broken strategies**, not
      as evidence of edge? Is the number of research trials behind it recorded
      somewhere, so selection bias can be corrected downstream?

## Gate 2 — Operational runtime

- [ ] Is the paper-trading duration $\ge$ the configured floor — and is it clear
      whether the count is calendar or trading days?
- [ ] Did the paper window contain at least one genuinely volatile session, or is a
      calm-market result being read as a stress result?
- [ ] Is `paper_trading_errors = 0` backed by error detection that would actually have
      caught an error?
- [ ] Has the kill switch been **fired** against this strategy in the paper
      environment, not merely wired to it?

## Gate 3 — Model risk

- [ ] Does the model card exist **and** document parameter limits, decay conditions
      and known failure modes? (The engine checks existence only.)

## Gate 4 — Compliance

- [ ] Is the compliance sign-off dated and attributable to a named person?
- [ ] For an EU/UK investment firm: has the deployment been authorised by a person
      designated by senior management (MiFID II RTS 6 Art. 5(2))? A compliance
      sign-off is not automatically that authorisation.

## The record itself

- [ ] Is `policy_applied` persisted **with** the verdict? A verdict alone cannot be
      audited — a zeroed config emits the same `ONBOARDING_PASSED` string.
- [ ] Is `policy_weakened` empty? If not, is every relaxation deliberate and justified
      in writing?
- [ ] Are `failed_gates` and `failed_criteria` recorded for a rejection, so the
      re-submission addresses the actual finding?
- [ ] Is anyone reading `total_gates_passed` as a score? 3/4 is a rejection.

## After a pass

- [ ] Are the RTS 6 Art. 8 predefined deployment limits set (instruments, order
      price/value/count, strategy positions, venues)?
- [ ] Is the initial capital allocation staged rather than full-size?
- [ ] Is a rollback trigger — back to paper — defined **before** the first live order?
