---
name: risk-control-latency-budget
description: >-
  Measure, budget, and audit live trading risk-control latency from event observation through decision, local dispatch, and optionally broker/exchange acknowledgement. Use for pre-trade checks, drawdown breakers, kill switches, position limits, cancel paths, regressions, and incident review; do not equate local submission with effective containment.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- latency-budget
- risk-control-sla
- circuit-breaker-latency
- performance-profiling
brokers_frameworks:
- Risk Control Latency Budgeter
- Python
version: 1.1.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building or auditing live risk management pipelines (e.g. kill switches, drawdown circuit breakers, position limit checks). A risk control that is logically correct but takes $2.5$ seconds to evaluate and submit a cancel-all order during a market collapse fails to protect capital. This skill profiles end-to-end risk evaluation latency and enforces SLA budgets (e.g. $<50$ ms for automated risk filters).

## Prerequisites

- Risk event evaluation timestamp $T_{\text{event}}$, evaluation start $T_{\text{start}}$, evaluation finish $T_{\text{finish}}$, and order sent $T_{\text{order\_sent}}$.
- Maximum allowable risk control SLA latency budget $L_{\text{budget\_ms}}$ (e.g. 50 ms).

## Workflow

1. **Record Timestamps Across Pipeline**:
   - Ingestion Delay: $\Delta t_{\text{ingest}} = T_{\text{start}} - T_{\text{event}}$
   - Evaluation Delay: $\Delta t_{\text{eval}} = T_{\text{finish}} - T_{\text{start}}$
   - Transmission Delay: $\Delta t_{\text{send}} = T_{\text{order\_sent}} - T_{\text{finish}}$

2. **Calculate Total Risk Latency**:
   $$\Delta t_{\text{total}} = \Delta t_{\text{ingest}} + \Delta t_{\text{eval}} + \Delta t_{\text{send}}$$

3. **Audit Against Latency SLA Budget**:
   Check if $\Delta t_{\text{total}} \le L_{\text{budget\_ms}}$.

4. **Alert SLA Violation & Trigger Fallback**:
   Flag slow risk controls and emit performance bottleneck breakdown.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Blocking DB / I/O Calls in Risk Loop**: Performing synchronous database writes or HTTP network queries inside the critical risk evaluation path.
- **Measuring Only Algorithm Execution Time**: Measuring only function CPU time while ignoring queue buffering and network transmission delays.

## Verification

- Submit risk breach event with 120 ms evaluation delay ($50$ ms SLA budget), verify SLA breach detection and bottleneck identification.
- Run `python scripts/test_risk_latency_budgeter.py` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `order-book-imbalance-signal-pipeline`
---
