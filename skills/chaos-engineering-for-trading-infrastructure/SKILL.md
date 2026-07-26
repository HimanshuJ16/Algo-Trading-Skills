---
name: chaos-engineering-for-trading-infrastructure
description: >-
  Quantitative infrastructure testing tool that injects controlled network latency, packet drops, and process terminations to validate trading system resilience.
domain: Infrastructure
subdomain: Reliability
tags: ["chaos-engineering", "resilience", "latency", "failover", "infrastructure"]
brokers_frameworks: ["Generic Infrastructure"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to proactively validate the resilience of a trading system. In quantitative trading, network jitter, dropped FIX packets, and unexpected process crashes are inevitable. Chaos engineering involves intentionally injecting these faults in a controlled environment (or carefully in production) to ensure failover mechanisms (like secondary gateways or heartbeat timeouts) trigger correctly before a real outage causes financial loss.

## Prerequisites

- A staging or paper-trading environment that perfectly mirrors production infrastructure.
- High-resolution observability (monitoring) to verify the system's reaction to the injected faults.

## Workflow

1. **Baseline Definition**: Define the steady-state of the system (e.g., "Order gateway processes 100 orders/sec with < 5ms latency").
2. **Fault Injection**: Use the `ChaosInjector` to simulate a specific failure:
   - **Jitter**: Inject random latency (e.g., 50-200ms) into the simulated network layer to test backpressure handling.
   - **Drop**: Simulate a severed TCP connection to test reconnect logic and sequence number gap-fills.
   - **Crash**: Forcibly terminate a simulated downstream service to test circuit breakers.
3. **Observation**: Monitor the system. If it fails to recover (e.g., it hangs indefinitely waiting for a dropped packet), the chaos experiment has surfaced a bug.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing in Production Without Limits**: Injecting a network partition on a live FIX connection during market hours without a "blast radius" constraint, leading to unmanaged open positions.
- **Ignoring Jitter**: Only testing hard crashes (process death). In trading, a "grey failure" (a connection that stays open but becomes extremely slow) is often more dangerous than a hard crash, as it bypasses standard TCP disconnect handlers.
- **Manual Execution**: Running chaos tests manually once a quarter. They must be automated in CI/CD pipelines against integration testing environments.

## Verification

- Initialize the `ChaosInjector` and wrap a mock network connection. Inject 100ms of latency and a 10% packet drop rate. Verify the downstream consumer experiences the delay and handles the missing packets gracefully.
- Run `python scripts/test_chaos_injector.py`.

## Related Skills

- `circuit-breaker-for-downstream-service-calls`
- `feed-handler-canary-deployment`
