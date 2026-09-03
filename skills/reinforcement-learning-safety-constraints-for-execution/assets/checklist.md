# Pre-Flight / Sign-off Checklist — reinforcement-learning-safety-constraints-for-execution

Use before allowing an RL execution policy to route live orders behind this shield.

## Placement

- [ ] **The shield is between the policy and the router**, and the policy has no other path
      to the order gateway.
- [ ] **Broker-side controls exist independently.** This guard is not the SEC Rule 15c3-5
      control — 15c3-5(d)(1) requires those to be under the broker-dealer's direct and
      exclusive control.
- [ ] **A kill switch exists above the shield.** The guard constrains each action; it cannot
      stop a strategy or cancel resting orders (RTS 6 Art. 12).
- [ ] **The gaps are known and covered elsewhere:** no price collar, no order-value limit,
      no message-rate limit, no repeated-execution throttle (RTS 6 Art. 15(1)(a), (b), (d),
      Art. 15(3)).

## Limits

- [ ] **`max_order_size` set per instrument**, not a single global default.
- [ ] **`max_inventory` set and reconciled against the broker's position**, not the agent's
      internal belief.
- [ ] **`max_spread` set per instrument** and sane for its tick size and liquidity tier.
- [ ] **`max_cumulative_qty` is set.** The default `None` leaves cumulative activity
      unconstrained; a per-order clip alone is circumvented by re-proposing every step.
- [ ] **`terminal_horizon_sec` matches the parent order's actual mandate**, and is long
      enough that `max_order_size` can actually flatten the inventory in the slices
      remaining. Check this arithmetic explicitly — the guard will not warn you.
- [ ] **`terminal_clearance_overrides_spread_veto` was decided deliberately**, and whoever
      owns the residual-inventory risk agreed to it.
- [ ] **Limits were agreed with risk, not chosen by the policy author**, and cannot be
      changed unilaterally (ESMA ¶73).

## Training contract

- [ ] **`proposed_qty`, not `safe_qty`, is stored in the replay buffer** alongside
      `shaped_reward`. This is the failure that teaches the policy to avoid safety.
- [ ] **`is_data_integrity_failure` steps are dropped from training**, not learned from.
- [ ] **`penalty_lambda` was tuned against the reward scale.** Too small and the policy
      learns to let the shield do the work; too large and it suppresses legitimate trading.
- [ ] **`reset_episode()` is called between parent orders**, or the budget leaks across them.
- [ ] **Convergence was checked empirically.** The shielded-MDP convergence result does not
      cover this continuous-action, reward-modifying variant.

## Data quality

- [ ] **Quotes come from the same snapshot the policy observed.**
- [ ] **Non-finite inputs and crossed books are confirmed to veto**, and the veto is
      alarmed. A `DATA_INTEGRITY` code is a feed fault, not a policy signal.
- [ ] **`DATA_INTEGRITY` rate is monitored separately** from policy interceptions.

## Monitoring

- [ ] **Interceptions feed real-time monitoring**, not only a log file (RTS 6 Art. 16;
      ESMA ¶94).
- [ ] **Alerting is by reason code**, not on the aggregate interception rate — the aggregate
      falls for good and bad reasons alike.
- [ ] **`TERMINAL_CLEARANCE` frequency is reviewed.** A policy that routinely has to be
      force-liquidated is not completing its mandate on its own.
- [ ] **`CUMULATIVE_BUDGET` hits are reviewed.** Repeated exhaustion means the policy is
      trying to trade more than the episode allows.

## Change control

- [ ] **Changing any limit is treated as a material change** requiring re-testing and
      risk/compliance involvement (ESMA ¶31, ¶73).
- [ ] **Retraining the policy is treated as a material change**, even with the shield
      untouched (ESMA ¶31).
- [ ] **Accumulated recalibration is reviewed on a cadence** — a series of small changes can
      become a material change untested (ESMA ¶30).
- [ ] **A documented exception process exists** for orders blocked but nevertheless required
      (RTS 6 Art. 15(6)): temporary, exceptional, verified by risk management. No limit was
      widened just to clear an alert backlog.

## Automated Testing

- [ ] Run `python -m unittest discover -s skills/reinforcement-learning-safety-constraints-for-execution/scripts` — 45 tests, 100%
      pass rate.
- [ ] Reducing an over-cap position, sign-crossing clamps, non-finite/crossed-book vetoes,
      precedence independence from the proposal, and post-deadline reduce-only all confirmed.

## Sign-off

- Reviewed by (risk): ___________________________
- Reviewed by (compliance): ___________________________
- Date: ___________________________
- Guard parameters deployed: ___________________________
- Policy version / training run: ___________________________
