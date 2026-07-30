---
name: execution-algorithm-kill-switch-integration
description: >-
  Quantitative risk and execution engine for integrating multi-level algorithmic kill switches (SEC Rule 15c3-5, MiFID II RTS 6), issuing FIX MassCancel requests, and locking order entry during risk breaches.
domain: Execution Algorithms
subdomain: Emergency Safety & Risk Controls
tags: ["kill-switch", "sec-rule-15c3-5", "mifid-ii-rts-6", "mass-cancel", "runaway-algo-protection", "risk-control", "emergency-shutdown"]
brokers_frameworks: ["SEC Rule 15c3-5", "MiFID II RTS 6", "FIX MassCancel Tag 530", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional trading gateways, algorithmic risk engines, and Smart Order Routers. Regulatory standards (**SEC Rule 15c3-5 Market Access Rule** and **MiFID II RTS 6**) mandate that automated execution algorithms possess hard-coded, sub-50ms **Kill Switches**. When triggered by max daily loss breaches, order loop anomalies ($> 100\text{ msgs/sec}$), or manual operator override, the engine must immediately halt order submission, issue FIX `MassCancelRequest` (Tag 530), and lock the system.

## Prerequisites

- Risk thresholds (max daily loss $L_{\text{max}}$, max order rate $N_{\text{max}}$, max net exposure $E_{\text{max}}$).
- Active strategy instances and open child orders across venues.
- Manual emergency override REST/FIX endpoint.

## Workflow

1. **Continuous Risk & Liveness Monitoring**:
   - Monitor daily realized + unrealized PnL, order message frequencies, and net position exposure.
2. **Kill Switch Trigger Audit**:
   - Evaluate trigger conditions:
     - `MAX_LOSS_BREACH`: Daily Loss $> L_{\text{max}}$.
     - `RUNAWAY_LOOP_DETECTED`: Order Rate $> 100\text{ msgs/sec}$.
     - `MAX_EXPOSURE_BREACH`: Net Exposure $> E_{\text{max}}$.
     - `MANUAL_OVERRIDE`: Emergency kill command received.
3. **Mass Cancel & System Lockdown Execution**:
   - Transition state to `KILL_SWITCH_ENGAGED`.
   - Issue FIX `MassCancelRequest` (Tag 530 = 7 `CANCEL_ALL_ORDERS`) across all connected gateways.
   - Reject all subsequent incoming order requests (`REJECTED_KILL_SWITCH_ACTIVE`).
4. **Audit Report Generation**: Output structured `KillSwitchAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Slow Out-of-Band Mass Cancellation**: Attempting to cancel orders one-by-one rather than issuing a single atomic FIX `MassCancelRequest` (Tag 530).
- **Failing to Block Subsequent Orders**: Triggering a kill switch on existing orders while allowing incoming strategy child orders to continue routing to venues.
- **Uncontrolled Runaway Algo Loops**: Lacking rate-limiting order loop detectors, allowing bugged algorithms to flood exchanges with thousands of orders per second.

## Verification

- Instantiate `ExecutionAlgoKillSwitchEngine`. Configure max daily loss limit = \$10,000. Simulate PnL drop to -\$12,500. Verify engine engages Kill Switch (`KILL_SWITCH_ENGAGED`), issues FIX `MassCancelRequest` for 5 active orders, and rejects subsequent new order placements with `REJECTED_KILL_SWITCH_ACTIVE`.
- Run `python scripts/test_execution_algorithm_kill_switch_integration.py`.

## Related Skills

- `execution-algo-behavior-under-halted-instrument`
- `disaster-recovery-runbook-for-full-region-outage`
---
