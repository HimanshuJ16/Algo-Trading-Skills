---
name: execution-algorithm-kill-switch-integration
description: >-
  Use when building the component that executes an emergency stop in an order gateway or
  algorithm: latches order entry shut before dispatching cancels, sends mass-cancel per
  venue and tracks acknowledgements. Authorisation happens upstream.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: kill-switch, sec-rule-15c3-5, mifid-ii-rts-6, fix-mass-cancel, runaway-algo-protection, risk-control, emergency-shutdown
  brokers_frameworks: "MiFID II RTS 6 (EU 2017/589); SEC Rule 15c3-5; FIX 4.4 / 5.0 SP2 OrderMassCancelRequest; Nasdaq Rule 6130 Kill Switch; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or reviewing the component that *executes* an emergency stop in an order gateway, execution algorithm, or Smart Order Router: it latches order entry shut and chases the orders already at the venues.

Two facts shape the engine, and both are why the naive version is dangerous:

- **The lockout and the cancel are not one action, and the order matters.** Latching order entry is local and cannot fail; cancelling is a network round trip to every venue. Dispatch cancels first and a looping algorithm refills the book through the gap. This engine latches first, always.
- **A cancel is a request, not a state change.** FIX `MassCancelRejectReason` (tag 532) value 0 is "Mass Cancel Not Supported", so a venue can refuse outright; venues also exclude orders from cancellation by rule (NYSE Pillar keeps MOO/LOO orders working after the auction cutoff). Orders therefore move `NEW/PARTIALLY_FILLED → PENDING_CANCEL → CANCELLED/FILLED`, and the report exposes `pending_cancel_order_ids` and `uncancelled_order_ids`. **Gate your escalation on those, never on `cancel_requested_count`.**

The regulatory pressure is real but less specific than kill-switch marketing suggests. MiFID II RTS 6 Art. 12(1) requires the firm to "cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues"; Art. 12(3) requires the firm to know which algorithm and which trader, desk or client owns each order at a venue. SEC Rule 15c3-5(b) requires documented risk controls, (c)(1)(i) pre-set credit/capital thresholds, and (d) that they be under the broker-dealer's "direct and exclusive control". **Neither rule states a latency figure, and 15c3-5 never uses the phrase "kill switch."**

## When NOT to Use

- **As the human authorisation gate.** This engine executes; it does not decide who may fire it. RBAC, four-eyes sign-off and break-glass tokens belong to `emergency-manual-override-access-control`, which hands an approved decision to this engine.
- **As the limit-setting policy.** Which drawdown matters, how PnL is computed, and how strategy-level limits ladder into portfolio-level ones are `kill-switch-and-drawdown-circuit-breakers`, `portfolio-level-stop-loss-independent-of-strategy-stops` and `strategy-level-kill-switch-vs-portfolio-level-kill-switch`. This engine consumes a `RiskSnapshot`; it computes no PnL of its own.
- **As a position flattener.** Cancelling every working order leaves the position you already hold completely untouched. A kill switch stops the bleeding from *new* trading; deciding whether to flatten, hedge or hold is a separate decision with its own market impact.
- **As a replacement for venue-side controls.** Every control here runs inside your process, so none of it works when your process is the thing that failed. Nasdaq Rule 6130's Kill Switch disables the MPID's order-entry ports from the exchange side, and cancel-on-disconnect kills resting orders when your session drops. Configure both; this engine is the third layer, not the only one.
- **For a halted or non-continuous instrument.** Cancel acceptance is phase-dependent — CME Globex forbids cancels in `Pre-Open - No Cancel`, Eurex T7 holds deletions pending during a freeze. That is `execution-algo-behavior-under-halted-instrument`.
- **As durable state.** The order map, kill state and audit trail are in memory. A restart silently drops every latch and every pending cancel. Persist them, or fail closed on start-up.

## Prerequisites

- Python 3.9+ (`from __future__ import annotations`, stdlib only — no dependencies).
- A `FirmRiskLimits` your firm has actually calibrated: max daily loss, max order rate per second, max net exposure. RTS 6 Art. 15(1) mandates that maximum order values, volumes and *message limits* exist; it publishes no numbers, and neither does 15c3-5. The calibration record is the audit artefact, not the number.
- One `KillSwitchGateway` per venue/FIX session. Without gateways the engine still latches the lockout, but reports every cancel as `NO_GATEWAY` and escalates — it never claims a cancel it did not send.
- A risk feed producing `RiskSnapshot(daily_pnl_usd=..., net_exposure_usd=..., as_of=...)`. **PnL is signed: negative is a loss.**
- A supervisory loop calling `evaluate_risk_state()` on a timer, and an `ExecutionReport` feed calling `apply_execution_report()` — nothing confirms a cancel on its own.
- An authorised human path for `reset()`. Nothing re-arms automatically.

## Workflow

1. **Gate every order through `audit_and_validate_new_order(req, snapshot)`.** It checks, in order: global latch → strategy latch → risk-data usability → snapshot staleness → limits.
   - **Decision point — unusable risk data fails closed for the order, not firm-wide.** A NaN PnL passes *every* threshold comparison silently (`float('nan') >= 10_000` is `False`), so the engine rejects the order with `REJECTED_RISK_DATA_INVALID` instead of evaluating it. It deliberately does *not* engage a firm-wide kill: a broken telemetry feed is proof the limit cannot be checked, not evidence the firm is losing money.
   - Every order-entry attempt is counted toward the rate window *before* any decision, so a rejection loop still trips the runaway limit — a loop that never reaches the book is still a message-rate problem under RTS 6 Art. 15(1).
2. **Run `evaluate_risk_state(snapshot)` on a timer as well.** A kill switch that only evaluates risk when an order arrives cannot fire once the algorithm stops submitting — which is exactly what a stuck algorithm holding a losing position does.
   - **Decision point — breach is `>=`, not `>`.** A loss that reaches the limit has breached it. Waiting for strictly-greater makes the limit itself the one loss you never stop.
3. **On breach or manual override, `trigger_kill_switch(scope, reason)` latches first, then dispatches.**
   - **Decision point — a scope the engine cannot parse raises.** An unknown scope, or `STRATEGY` with no `strategy_id`, raises `KillSwitchError`. It must never return a report reading `NORMAL_OPERATIONS`, because a mistyped kill-switch call that looks like success is worse than no kill switch at all.
   - **Decision point — GLOBAL fans out to every configured gateway, not just venues with known orders.** The local order map is what the firm *believes* is live; a missed `ExecutionReport` means an order you cannot see. Art. 12(1) is about the orders at the venue.
   - **Decision point — STRATEGY scope cancels order-by-order.** FIX tag 530's narrowest scope is a *security* (value 1), which would cancel other strategies' orders in that symbol and miss this strategy's orders in every other symbol. `fix_mass_cancel_tag_530` is therefore `None` for strategy scope. Where a venue supports the FIX 5.0 SP2 `TargetParties` component on `OrderMassCancelRequest`, put the party-scoped mass cancel in your gateway adapter — never relabel tag 530.
4. **Read the dispatch result, not the request count.** A rejected or errored venue leaves its orders in `uncancelled_order_ids` and sets `manual_intervention_required`. One venue's transport failure never stops the others.
5. **Confirm through `apply_execution_report()`.** `PENDING_CANCEL → CANCELLED` is the only thing that means an order is dead. A `FILLED` arriving for a `PENDING_CANCEL` order logs a warning: it traded in the race, so post-kill exposure changed and the flattening decision needs revisiting.
6. **Re-arm only through `reset(scope, authorized_by=..., reason=...)`.** It refuses while any cancel is unconfirmed or any dispatch failed, unless a named human passes `acknowledge_unconfirmed=True` after reconciling the venue book by hand.
   - **Decision point — resetting this engine does not lift a venue-side kill switch.** Under Nasdaq Rule 6130 the participant must ask Nasdaq operations to reactivate the MPID's ports. Re-arming locally while the venue is still shut produces a silent, one-sided outage.

> Full procedure: see `references/workflows.md`.
> Standards and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting an order as cancelled the moment the request is sent.** The venue may answer tag 532=0 "Mass Cancel Not Supported", may be unreachable, or may exclude the order by rule. Marking orders `CANCELLED` on dispatch produces a false audit record and a risk system that believes exposure it still has is gone.
- **Passing a positive number as the loss.** `daily_pnl_usd` is signed. Wire a loss magnitude into it and the comparison becomes `+12500 <= -10000` — the loss limit can then never trigger, and the failure is completely silent. (This is a real breaking change from 1.0.0, which took a positive `current_daily_loss_usd`.)
- **Letting NaN through the risk gate.** Every comparison against NaN is `False`, so a broken PnL feed does not trip a single limit. Validate finiteness explicitly and fail closed; a limit you cannot evaluate is not a limit you passed.
- **Cancelling before latching order entry.** The algorithm keeps submitting into the gap while your cancels are in flight, and you cancel orders it replaces faster than you kill them.
- **Believing one `MassCancelRequest` is atomic across venues.** It is scoped to one FIX session at one venue. ESMA's Q&A on Art. 12 is explicit that kill functionality need not be a single unified piece of software — but it must be reachable by a single decision, which means fanning out and *aggregating the failures*, not assuming one message covered everything.
- **Using tag 530=1 for a strategy-scoped kill.** Value 1 is "cancel orders for a security": it over-cancels every other strategy in that symbol and under-cancels the target strategy everywhere else. Both errors are silent.
- **Auto-resetting after a cool-down.** The condition that fired the switch is a bug or a market state, and neither is cleared by a timer. Re-arm on a human decision, recorded with an identity and a reason (15c3-5(b) and (e) make the controls and their review a books-and-records matter).
- **Treating the kill switch as a flatten.** Cancelling working orders does nothing to the position already on the book.
- **Check-then-act with no lock.** A kill switch lives in a concurrent order path; an unsynchronised `if not engaged: submit()` leaks orders out after the latch closes.
- **Only ever testing the happy path.** The interesting cases are the venue that refuses, the gateway that throws, the order at a venue with no configured gateway, and the fill that lands after the cancel was accepted.

## Verification

- Instantiate `ExecutionAlgoKillSwitchEngine(limits, {"NYSE": gateway}, clock=...)` with a frozen clock (the test module has one) and `max_daily_loss_usd=10_000`, register two live orders, then call `evaluate_risk_state(RiskSnapshot(daily_pnl_usd=-12_500.0))`. Expect `status == "KILL_SWITCH_ENGAGED"`, `trigger_reason_code == "MAX_LOSS_BREACH"`, `cancel_requested_count == 2`, `fix_mass_cancel_tag_530 == "7"`, and both orders in `PENDING_CANCEL` — **not** `CANCELLED`.
- Pass `daily_pnl_usd=+50_000.0`: expect `NORMAL_OPERATIONS` (a profit is not a breach). Pass `-10_000.0` exactly: expect a breach. Pass `-9_999.99`: expect none.
- Point the engine at a gateway returning `MassCancelOutcome(accepted=False, reject_reason="0")`: expect order entry still locked, the order still `NEW`, its id in `uncancelled_order_ids`, and `manual_intervention_required is True`.
- Give one of two gateways `raises=ConnectionError`: expect the healthy venue's orders `PENDING_CANCEL` and only the broken venue's order uncancelled.
- Submit `RiskSnapshot(daily_pnl_usd=float("nan"))`: expect `REJECTED_RISK_DATA_INVALID` and `is_global_kill_switch_engaged is False`.
- Call `trigger_kill_switch("PORTFOLIO", ...)` or `SCOPE_STRATEGY` with no `strategy_id`: expect `KillSwitchError`, and the engine still un-engaged.
- Fire a strategy-scoped kill: expect `fix_mass_cancel_tag_530 is None`, only that strategy's orders cancelled, and other strategies still passing the gate.
- Call `reset()` with cancels outstanding: expect `KillSwitchError`; confirm both cancels via `apply_execution_report(..., "CANCELLED")` and expect the reset to succeed and orders to flow again.
- Run `python -m unittest discover -s skills/execution-algorithm-kill-switch-integration/scripts` — 79 tests, 100% pass rate.

## Related Skills

- `emergency-manual-override-access-control`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `kill-switch-and-drawdown-circuit-breakers`
- `execution-algo-behavior-under-halted-instrument`
- `broker-api-idempotent-cancel-requests`
- `disaster-recovery-runbook-for-full-region-outage`
