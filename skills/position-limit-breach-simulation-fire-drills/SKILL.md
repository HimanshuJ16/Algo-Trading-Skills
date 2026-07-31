---
name: position-limit-breach-simulation-fire-drills
description: >-
  Operational risk fire drill simulation engine testing real-time risk gateway order rejections, kill switch activations, and CFTC compliance alerts during simulated position limit breaches.
domain: Regulatory Compliance & Risk Controls
subdomain: Operational Risk Simulation & Compliance Fire Drills
tags: ["fire-drill", "position-limits", "cftc-compliance", "risk-gateway", "kill-switch", "operational-risk", "simulation"]
brokers_frameworks: ["CFTC Part 150 Speculative Limits", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing and testing pre-trade risk gateways, automated kill switches, and compliance alerting pipelines. Regulatory mandates (CFTC Part 150, CME Rule 559, ESMA MiFID II) penalize firms severely for position limit breaches (including intraday spikes). Routine operational "Fire Drills" inject simulated breach orders (`EXCHANGE_LIMIT`, `BROKER_LIMIT`, `ROGUE_ALGO`) into staging environments to verify that risk gateways reject orders within sub-5ms SLAs and activate kill switches without risking live capital.

## Prerequisites

- Fire drill scenario definition (`scenario_id`, `breach_type`, `target_symbol`, `injected_position_qty`, `limit_threshold`).
- Configured simulator parameters (`enabled`, `max_allowed_risk_latency_ms`: default 5.0).

## Workflow

1. **Fire Drill Scenario Injection**:
   - Inject synthetic breach order into risk gateway test harness.
2. **Pre-Trade Risk Gateway Audit**:
   - Verify order is blocked before reaching broker/exchange interface.
   - Measure risk evaluation latency ($T_{\text{risk}} \le 5.0\text{ ms}$).
3. **Kill Switch & Escalation Verification**:
   - Confirm automated kill switch trips and locks strategy.
   - Confirm compliance alert notification is logged.
4. **Audit Report Generation**: Output structured `FireDrillReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing Only End-of-Day Limits**: Failing to test intraday real-time position limits (CFTC violations occur intraday, not just at session close).
- **Running Fire Drills in Live Trading**: Accidentally routing simulated breach orders to live production exchange matching engines.
- **Unmeasured Risk Latency**: Verifying order rejection without recording risk gateway evaluation latency ($> 50$ms latency causes queuing under fast market conditions).

## Verification

- Instantiate `FireDrillSimulator`. Inject `EXCHANGE_LIMIT` scenario ($12,000$ contracts vs $10,000$ CFTC limit) with $1.2$ms risk latency $\implies$ verify `BREACH_BLOCKED_KILL_SWITCH_ENGAGED` status, latency SLA pass, and compliance alert generated.
- Run `python scripts/test_fire_drill_simulator.py`.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `leverage-limit-enforcement-across-instruments`
---
