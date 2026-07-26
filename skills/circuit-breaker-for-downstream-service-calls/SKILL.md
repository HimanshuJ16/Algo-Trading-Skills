---
name: circuit-breaker-for-downstream-service-calls
description: >-
  Resilience engineering pattern implementing a Circuit Breaker to prevent cascading failures when downstream trading services or data APIs experience degraded performance.
domain: Infrastructure
subdomain: Reliability
tags: ["circuit-breaker", "resilience", "microservices", "api", "fail-fast"]
brokers_frameworks: ["Generic Infrastructure"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when your trading system relies on external or downstream microservices (e.g., historical data APIs, alternative data vendors, or non-critical risk checks). If that downstream service becomes slow or unresponsive, a Circuit Breaker immediately fails fast, preventing your main trading threads from blocking, exhausting connection pools, and causing a cascading system-wide outage.

## Prerequisites

- A solid understanding of the State Machine pattern (Closed, Open, Half-Open).
- An asynchronous or multi-threaded trading architecture where non-critical service calls can fail gracefully.

## Workflow

1. **Closed State**: The circuit is closed, and requests flow normally. If a request fails (timeout or HTTP 5xx), the failure counter increments.
2. **Open State**: If the failure threshold is reached (e.g., 5 consecutive failures), the circuit "trips" to the Open state. Further requests immediately raise a `CircuitBreakerOpenException` without actually hitting the network, saving resources and allowing the downstream service to recover.
3. **Half-Open State**: After a configurable timeout (e.g., 30 seconds), the circuit transitions to Half-Open. A limited number of test requests are allowed through. 
   - If they succeed, the circuit resets to Closed. 
   - If they fail, the circuit returns to Open.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Wrapping Critical Path Functions**: Do not wrap your primary exchange execution gateway in a circuit breaker if it means silently dropping live orders. Circuit breakers are for services where degradation is preferable to total system failure.
- **Infinite Half-Open Loops**: If the downstream service flickers, the circuit might thrash between Open and Half-Open. Ensure exponential backoff is implemented for the recovery timeout.
- **Ignoring Exceptions**: Swallowing the `CircuitBreakerOpenException` without alerting the monitoring system.

## Verification

- Wrap a mock API call that always throws a `TimeoutError`. Call it 5 times to trip the breaker. On the 6th call, verify that a `CircuitBreakerOpenException` is thrown immediately without executing the mock API logic.
- Run `python scripts/test_circuit_breaker.py`.

## Related Skills

- `chaos-engineering-for-trading-infrastructure`
- `smart-order-router-failover-on-venue-outage`
