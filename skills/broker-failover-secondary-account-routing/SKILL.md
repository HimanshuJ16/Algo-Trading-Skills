---
name: broker-failover-secondary-account-routing
description: Institutional-grade circuit breaker and failover router for high-availability
  algorithmic trading systems.
version: 1.1.0
domain: algorithmic-trading
subdomain: general
tags:
- trading
- algo
- skill
brokers_frameworks:
- Python
- Dataclasses
author: algo-trading-skills-contributors
license: Apache-2.0
---

# Broker Failover & Secondary Account Routing

This skill provides a rigorously engineered **Broker Failover Router** leveraging the Circuit Breaker design pattern. It ensures robust continuation of algorithmic order flow by dynamically rerouting to a secondary broker account when primary API connections degrade, experience latency spikes, or return critical HTTP 503/429 errors.

## Core Features
1. **Circuit Breaker Pattern (CLOSED, OPEN, HALF_OPEN):** Protects primary connections from cascading failures.
2. **Concurrency Safe:** Built-in threading locks handle concurrent order streams from multiple alpha models without race conditions.
3. **Automated Half-Open Probing:** After a predefined exponential backoff/timeout period, the system probes the primary broker with a single test order.
4. **Symbol Translation Mapping:** Normalizes canonical tickers to broker-specific formats (e.g., `AAPL` -> `AAPL STK SMART` vs `AAPL.S`).

## Integration Checklist
Check the `assets/checklist.md` for a comprehensive onboarding guide to integrate this into your institutional quant infrastructure.

## Folder Structure
- `scripts/`: Contains the core `failover_router.py` implementation and `test_failover_router.py` unit tests.
- `references/`: Documentation on institutional routing standards and sequence workflows.
- `assets/`: Integration checklists.

*Note: For production deployment, ensure the timeout values and backoff mechanisms are tuned to the specific exchange latency characteristics.*


## When to Use

Documentation for When to Use.


## Prerequisites

Documentation for Prerequisites.


## Workflow

Documentation for Workflow.


## Common Pitfalls

Documentation for Common Pitfalls.


## Verification

Documentation for Verification.


## Related Skills

Documentation for Related Skills.
