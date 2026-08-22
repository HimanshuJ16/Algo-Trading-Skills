# Workflows for Strategy Canary Releases

The phases below are gates, not a calendar. Durations are deliberately absent: two weeks
of a quiet market exercises less than one volatile hour, and a fixed schedule invites
promotion by elapsed time rather than by evidence. Choose each phase's exit criteria from
the strategy's own trading frequency and the regimes it must survive, write them down
before the phase starts (`assets/checklist.md`), and hold to them.

## Phase 0 — Before anything is registered

1. Establish the **baseline** the change is measured against: the current production
   version's live behaviour, or, for a genuinely new strategy, the backtest that
   justified building it.
2. Collect the **venue rules per instrument**: lot step, minimum quantity, minimum
   notional, tick size, and what the venue does with orders below one lot. These are
   distinct constraints; a venue can require a minimum of 500 shares in steps of 100.
3. Write the promotion criteria and the abort criteria. Name the person who decides.
4. Confirm the kill switch works and is independent of the router. Demoting a strategy to
   SHADOW does not cancel resting orders or flatten positions.

## Phase 1 — SHADOW

```python
router.register_strategy(StrategyRegistration(
    strategy_id="momentum_v3",
    phase=DeploymentPhase.SHADOW,
    min_lot_size=100,          # venue quantity step
    min_quantity=100,          # venue minimum quantity (often, but not always, equal)
))
```

- The strategy consumes live market data and computes signals; the router suppresses
  every order. Each decision returns `RoutingAction.SUPPRESSED` with the
  `requested_quantity` the strategy wanted — record that as a hypothetical fill, in a
  store **physically separate** from live executions.
- Verify at the gateway, not in configuration, that zero order messages leave the process
  for this strategy over a full session.
- What to look at: signal count and timing versus the backtest, instrument coverage,
  duplicate or contradictory signals, behaviour across the open, the close and any
  auction, and the ingress-to-signal latency profile.
- **Exit criteria**: enough signals for the comparison to mean something; live signal
  behaviour consistent with the backtest that justified the change; no unexplained
  divergence in rate, timing or universe. A shadow run that disagrees with the backtest
  is a finding — investigate it here, where it is free.

## Phase 2 — CANARY

```python
router.set_phase("momentum_v3", DeploymentPhase.CANARY, authorised_by="head.of.trading")
```

with the registration carrying the limits that actually bound the risk:

```python
StrategyRegistration(
    strategy_id="momentum_v3",
    phase=DeploymentPhase.CANARY,
    canary_scale_factor=0.05,             # relative: 5% of requested size
    min_lot_size=100,
    min_quantity=100,
    min_notional=1_000.0,                 # venue rule, if any
    max_canary_order_notional=25_000.0,   # absolute, per order
    canary_notional_budget=500_000.0,     # absolute, cumulative for this canary run
)
```

- Orders are floored to the lot step after scaling, then checked against the minimum
  quantity, the minimum notional and the remaining budget, in that order. Anything that
  fails comes back `REJECTED` with the failing constraint named in
  `binding_constraint` — route those to an alert, and treat `registration` (an
  unregistered strategy reaching the order path) as an incident, not a log line.
- **Monitor which constraint binds.** A canary whose orders are consistently reduced by
  `max_canary_order_notional` is sampling only the small end of the strategy's order
  distribution, and its slippage statistics do not generalise.
- **Budget accounting is submission-based.** The router never sees fills. Call
  `release_notional(strategy_id, amount)` when the venue rejects an order or you cancel
  it unfilled; do not call it for filled orders. Use `consumed_notional()` for a live
  view, and `reset_canary_budget(strategy_id, authorised_by=...)` — an attributed,
  recorded act — when an operator decides to extend the run.
- What to measure: rejection codes and rates, order state transitions, round-trip
  latency, fee and rebate treatment, borrow availability for shorts, reconciliation
  against the broker's positions, and realised slippage versus the execution model on
  liquid names.
- What **not** to conclude: anything from canary PnL. At 5% size it is noise scaled by
  0.05. Market impact, queue dynamics at size, capacity and margin interactions are also
  invisible here — they are what the first full-size session is for.
- **Exit criteria**: a pre-agreed number of live orders across the regimes that matter;
  rejection rate at or below the agreed threshold; slippage within the modelled band;
  clean reconciliation; no unexplained divergence from the shadow-phase behaviour.
- **Abort criteria**: demote to SHADOW with `set_phase(..., authorised_by=...)`. No
  `force` is needed to reduce exposure, ever. If the problem is *live orders already at
  the venue*, use the kill switch — demotion does not touch them.

## Phase 3 — PRODUCTION

```python
router.set_phase("momentum_v3", DeploymentPhase.PRODUCTION, authorised_by="head.of.trading")
```

- Canary limits no longer apply. The strategy's exposure is now bounded only by the
  firm's real pre-trade risk layer and its kill switches — which is why those must exist
  independently of this router (see `references/standards.md`, sections 1 and 3).
- Monitoring stays intensive through at least the first full session at size: this is the
  first time market impact, capacity and portfolio interaction are observable at all.
- A direct SHADOW → PRODUCTION promotion is refused unless `force=True` is passed, which
  is recorded as a forced transition. The legitimate use is a rollback to a version that
  was already proven in production; using it to skip an inconvenient canary is how the
  guard becomes decoration.
- Export `phase_history` and retain it with the change record. It carries, per entry: the
  timestamp, the action (including refusals), the from/to phases, the authoriser, and
  whether the transition was forced.

## Composition with other deployment primitives

- **Replacing a version that holds positions** is a cutover problem first: run
  `blue-green-deployment-for-live-strategy-updates` to move routing authority with the
  book intact, then keep the new version in CANARY until it earns full size.
- **Capital scaling after promotion** — growing from full size on small capital to target
  capital — is a separate ramp; see
  `incremental-capital-deployment-for-new-strategies`.
- **Automated demotion** on anomaly detection composes cleanly with `set_phase()`; see
  `automated-rollback-triggers-on-anomaly-detection`. Wire the automated path to
  *demotion only*, never to promotion.
