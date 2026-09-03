# Workflows — reinforcement-learning-safety-constraints-for-execution

## 0. Where the shield sits

```
policy(observation) -> proposed_qty
                          |
                   SafeRLExecutionGuard.intercept_action(proposed_qty, state, base_reward)
                          |
        +-----------------+------------------+
        |                                    |
   safe_qty  -> order router -> broker   shaped_reward + proposed_qty -> replay buffer
                          |
                  broker-side pre-trade controls (SEC 15c3-5 / RTS 6)  <-- NOT this module
```

The guard is one layer. It never removes the need for broker-side market-access controls,
and it never removes the need for a kill switch above it.

## 1. Per-step guard sequence

Applied in this order on every step, unconditionally.

1. **Data-integrity gate.** Collect *all* problems, then veto to `0.0` if any exist:
   non-finite `proposed_qty`, `current_inventory`, `bid`, `ask` or `time_remaining_sec`, or
   a crossed book (`ask < bid`).
   - Emit `logger.error`, set `is_data_integrity_failure = True`, apply **no** penalty.
   - Rationale: every guard below is a `value > limit` comparison, and `NaN > limit` is
     `False`. Bad data does not trip guards; it silently removes them.
2. **Terminal inventory clearance.** `terminal_clearance_active` is
   `time_remaining_sec <= terminal_horizon_sec and current_inventory != 0` — a fact about
   the state alone. When active, the target is
   `copysign(min(|inventory|, max_order_size), -inventory)`. Record
   `TERMINAL_CLEARANCE` only if that differs from what was proposed (compared with
   `math.isclose`), so a policy that liquidates correctly is neither flagged nor penalised.
3. **Spread veto.** If `spread > max_spread` and the working quantity is non-zero, veto to
   `0.0` — unless `terminal_clearance_active and terminal_clearance_overrides_spread_veto`.
4. **Max order size.** `if |qty| > max_order_size: qty = copysign(max_order_size, qty)`.
5. **Position cap.**
   ```
   lo = min(-max_inventory, inventory)     # widened so an over-cap position stays reducible
   hi = max( max_inventory, inventory)
   if time_remaining_sec <= 0:             # after the deadline: reduce-only, no overshoot
       lo = max(lo, min(0, inventory))
       hi = min(hi, max(0, inventory))
   projected = inventory + qty
   if projected outside [lo, hi]:
       qty = clamp(projected, lo, hi) - inventory
   ```
   Record `HORIZON_EXPIRED` when the deadline has passed, `POSITION_CAP` otherwise.
6. **Cumulative quantity budget.** If `max_cumulative_qty` is set and no terminal clearance
   is active, clip `|qty|` to `max(max_cumulative_qty - cumulative_qty_routed, 0)`. Then
   accumulate `cumulative_qty_routed += |qty|`.
7. **Reward shaping.** `penalty = penalty_lambda if reason_codes else 0.0`;
   `shaped_reward = base_reward - penalty`. One penalty per step, never one per constraint.

Normalise `-0.0` to `0.0` before returning: the two compare equal but a `-0.0` quantity
reverses `copysign` and sign-based side mapping in a downstream router.

## 2. Training loop integration

```python
guard = SafeRLExecutionGuard(
    max_order_size=...,        # per-instrument, agreed with risk
    penalty_lambda=...,        # tuned against the reward scale, not the risk appetite
    terminal_horizon_sec=...,  # from the parent order mandate
    max_cumulative_qty=...,    # set this; None means unconstrained
)

for episode in episodes:
    guard.reset_episode()                       # clears the budget, keeps lifetime counters
    for step in episode:
        proposed = policy(observation)
        action = guard.intercept_action(proposed, state, base_reward)

        if action.is_data_integrity_failure:
            continue                            # environment fault: do not train on it

        env.execute(action.safe_qty)            # route the corrected quantity
        buffer.add(observation, action.proposed_qty, action.shaped_reward, next_obs)
        #                      ^^^^^^^^^^^^^^^^^^^ the proposal, never safe_qty
```

**The single most important line is the buffer write.** The punishment exists to teach the
policy that *its* proposal was unsafe. Pairing `shaped_reward` with `safe_qty` teaches the
policy that the safe action is the bad one, and it learns to avoid safety.

If you would rather not modify the reward at all, set `penalty_lambda=0.0` and store
`safe_qty` — that is the second option in Alshiekh et al., under which unsafe proposals
simply persist behind the shield. Do not mix the two.

## 3. Live deployment

1. **Reconcile inventory before every decision.** The cap is only as sound as
   `current_inventory`. Feed the broker's position, not the agent's belief.
2. **Take `bid`/`ask` from the same snapshot the policy observed.** A quote from a different
   instant makes the spread veto measure a spread nobody could have traded.
3. **Route interceptions to real-time monitoring, not just to a log file.** RTS 6 Art. 16
   requires real-time monitoring for signs of disorderly trading, and ESMA ¶94 states the
   monitoring should include the alerts from triggering pre-trade controls.
4. **Alert on reason-code mix, not on the aggregate rate.** A falling interception rate can
   mean a safer policy, a policy that has learned to sit just inside the limits, or a market
   that stopped producing the triggering states. `DATA_INTEGRITY` in particular is a feed
   alarm, not a policy signal — page on it separately.
5. **Keep the shield above the policy in the deployment topology.** If the policy can be
   updated without the shield's limits being re-reviewed, the limits are not hard blocks.

## 4. Change control

- Changing `max_order_size`, `max_inventory`, `max_spread`, `terminal_horizon_sec`,
  `max_cumulative_qty` or `terminal_clearance_overrides_spread_veto` is a **risk-control
  change** — ESMA ¶31 lists "Changing thresholds, kill switch logic, or alert triggers" as
  warranting retesting, and ¶73 expects risk management and compliance to be involved.
- Retraining the policy is a **material change** in its own right (¶31, "Retraining or
  modifying machine learning components"), even when the shield is untouched.
- An online-learning policy changes continuously. ESMA ¶30 warns that accumulated
  recalibrations can become a material change "without it being tested" — set a review
  cadence rather than a per-change trigger.
- If an order must be submitted despite being blocked, use a documented exception process
  (RTS 6 Art. 15(6)): temporary, exceptional, and verified by risk management. Never widen a
  limit to clear an alert backlog.

## 5. Verification checklist for a change to this guard

Re-run `python -m unittest discover -s skills/reinforcement-learning-safety-constraints-for-execution/scripts` and confirm, at minimum:

- A reducing order from an over-cap position still passes unintercepted.
- A sign-crossing order still clamps to the far band edge, not to same-side headroom.
- The routed quantity is still identical across proposals `{0, 50, -500, -250, 900}` in the
  wide-spread terminal state, under both settings of the override flag.
- Every non-finite input and the crossed book still veto to `0.0` with no penalty.
- A forced liquidation still routes with the cumulative budget fully spent.
