# Canary Release Sign-Off Checklist

Strategy ID: ____________  Version / change: ____________
Phase being entered: SHADOW / CANARY / PRODUCTION  Date/Time (UTC): ____________
Authorised by (named person): ____________

## Before SHADOW

- [ ] Change classified: is this new or **materially changed** strategy logic? If it is a
      parameter change your system can reload, say so and skip the ceremony.
- [ ] Baseline recorded: the production version's current live behaviour, or the backtest
      that justified a new strategy.
- [ ] Venue rules captured **per instrument**: lot step, minimum quantity, minimum
      notional, and what the venue does with an order below one lot (rejection? separate
      odd-lot market? executable but unrepresentative?).
- [ ] Promotion **and** abort criteria written down, in samples and execution behaviour —
      not elapsed time, not canary PnL. Decision-maker named.
- [ ] Kill switch tested and confirmed independent of this router. (Demotion to SHADOW
      stops new orders only; it cancels nothing already resting at the venue.)
- [ ] Shadow signal store confirmed **physically separate** from live executions, PnL,
      positions and tax lots.
- [ ] Strategy registered under a stable `strategy_id` that is carried onto the order, so
      live activity is attributable to this strategy.

## Exiting SHADOW → entering CANARY

- [ ] **Zero outbound order messages verified at the gateway** for the whole shadow
      period, not only in application config.
- [ ] Shadow signal rate, timing and instrument coverage compared against the backtest;
      any divergence explained, not noted.
- [ ] Enough signals accumulated for the promotion criteria to be statistically
      meaningful at all.
- [ ] `canary_scale_factor` set **and** justified (not left at the 5% default by default).
- [ ] `max_canary_order_notional` set from what a *defect* could cost, not from normal
      order size.
- [ ] `canary_notional_budget` set, with an explicit end date for the canary run.
- [ ] Scaled size sanity-checked per instrument: does the scaled order clear the lot step,
      the minimum quantity **and** the minimum notional, and will it execute in a way that
      represents full size?
- [ ] Named authoriser passed to `set_phase()`. **EU/UK firms in scope: RTS 6 Art. 5(2)
      makes this mandatory, not a formality.**
- [ ] Alerting wired on `RoutingAction.REJECTED`, with `binding_constraint ==
      "registration"` (an unregistered strategy on the order path) escalated as an
      incident.

## During CANARY

- [ ] Which constraint is binding, reviewed daily. If it is always
      `max_canary_order_notional`, the canary is sampling only small orders — say so
      before drawing conclusions from its slippage.
- [ ] `release_notional()` called for venue-rejected and cancelled-unfilled orders; not
      called for filled ones.
- [ ] Rejection codes and rates reviewed against expectation.
- [ ] Order state transitions inspected for stuck, orphaned or duplicated orders.
- [ ] Latency profile compared against the shadow phase.
- [ ] Fees, rebates and borrow costs reconciled against the model.
- [ ] Broker positions reconciled against internal state at least daily.
- [ ] Heightened monitoring maintained for the whole phase (EU/UK: RTS 6 Art. 8;
      US members: FINRA RN 15-09 §II).
- [ ] Canary PnL explicitly **excluded** from the promotion decision.

## Exiting CANARY → entering PRODUCTION

- [ ] Every written promotion criterion met, listed individually with its measured value.
- [ ] Regimes covered by the canary named (and the ones it never saw, named too).
- [ ] No unexplained divergence between shadow, canary and backtest behaviour.
- [ ] Pre-trade risk limits confirmed to be in force for full size, and owned by the risk
      layer rather than by the strategy. **US broker-dealers: SEC Rule 15c3-5 controls
      must be pre-trade and under the broker-dealer's direct and exclusive control.**
- [ ] Named authoriser passed to `set_phase()`; promotion is one step
      (CANARY → PRODUCTION), not a forced jump.
- [ ] Any use of `force=True` justified in writing, second person informed, recorded.
- [ ] Intensive monitoring scheduled for the first full session at size — market impact,
      capacity and portfolio interaction become visible only now.
- [ ] Rollback path agreed: who demotes, on what trigger, and whether the kill switch is
      needed instead.

## After every phase change

- [ ] `phase_history` exported and retained with the change record: authoriser present on
      each entry, refusals included, forced transitions flagged.
- [ ] Broker positions reconciled.
- [ ] Shadow/canary data retained and clearly labelled as non-production.
- [ ] If the canary was abandoned: strategy demoted, budget zeroed, and the reason
      recorded — an unpromoted canary left running is a strategy nobody owns.
