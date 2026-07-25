---
name: circuit-breaker-for-downstream-service-calls
description: >-
  Use when connecting trading engines to downstream microservices (database, risk service, portfolio ledger) to execute the Circuit Breaker pattern (CLOSED -> OPEN -> HALF_OPEN), preventing cascading latency and tick-processing thread starvation.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "circuit-breaker", "resilience", "fail-fast", "cascading-failure", "fault-tolerance"]
brokers_frameworks: ["Circuit Breaker Engine", "Python Decorators"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing real-time strategy loops calling downstream microservices or databases (e.g. Risk Server, PostgreSQL Audit DB, Compliance Gateway). If a downstream dependency experiences high latency, database lock contention, or network outages, un-isolated synchronous calls will block the trading engine loop, cascading into dropped market ticks and missed execution windows. The Circuit Breaker pattern trips `OPEN` on consecutive failures, enabling instant fail-fast or fallback responses.

## Prerequisites

- Target downstream service endpoint or function call.
- Configurable failure threshold (e.g., 3 consecutive failures) and cooldown period (e.g., 5.0 seconds).

## Workflow

1. **State Machine Transitions**:
   - `CLOSED`: Normal operation. All downstream calls pass through. Track consecutive errors and execution timeouts.
   - `OPEN`: Failure threshold breached. All calls immediately fail-fast or return cached fallback without making network calls.
   - `HALF_OPEN`: Cooldown period expired. Allow trial requests to verify downstream recovery.

2. **Intercept Downstream Failures & Timeouts**:
   - Record call exceptions and response latency timeouts. If consecutive errors $\ge N_{\text{fail}}$, trip state to `OPEN`.

3. **Provide Fallback Mechanism**:
   - Supply fast, non-blocking fallback (e.g., local cached risk check, async logging queue) when circuit breaker is `OPEN`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Fallback Logic**: Failing fast without providing a safe fallback response, causing caller code to raise unhandled exceptions.
- **Overly Short Cooldown Windows**: Setting `cooldown_seconds` too low (e.g., 100ms), causing constant rapid tripping without giving the downstream service time to recover.
- **Global Breaker Scope**: Sharing a single circuit breaker across all distinct microservices, so a DB failure trips the Risk Check service.

## Verification

- Simulate 3 consecutive downstream service failures and verify breaker state trips to `OPEN`.
- Verify fast fail-fast behavior while `OPEN` without invoking downstream function.
- Run `python scripts/test_circuit_breaker.py` and confirm 100% pass rate.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `grpc-streaming-for-internal-service-communication`
- `broker-status-page-monitoring-integration`
---
