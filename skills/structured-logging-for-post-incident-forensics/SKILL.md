---
name: structured-logging-for-post-incident-forensics
description: Use when designing log schemas for trading systems that enable post-incident
  timeline reconstruction without guesswork, using structured JSON log events with
  correlation IDs, sequence numbers, and standardized event types.
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment
- logging
- forensics
- incident-response
- structured-logs
- observability
brokers_frameworks:
- Python logging
- JSON
- ELK Stack
- Datadog
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building or retrofitting a trading system's logging infrastructure.
Unstructured text logs (print statements, ad-hoc messages) make post-incident forensics
extremely difficult — you can't filter, correlate, or reconstruct timelines programmatically.
This skill implements:
- Structured JSON log events with standardized fields.
- Correlation IDs linking related events across order lifecycle.
- Monotonic sequence numbers for ordering guarantee.
- Event type taxonomy (ORDER_PLACED, FILL_RECEIVED, RISK_BREACH, etc.).

## Prerequisites

- Python's `logging` module or equivalent structured logger.
- Defined event type taxonomy for the trading system.
- Log sink (file, stdout, or centralized log aggregator).

## Workflow

1. **Define Event Schema**: Standardize fields (timestamp, event_type, correlation_id, etc.).
2. **Instrument Code**: Replace print/ad-hoc logging with structured event emissions.
3. **Assign Correlation IDs**: Link all events in an order's lifecycle with a single ID.
4. **Sequence Numbering**: Monotonically increasing sequence for ordering.
5. **Query & Reconstruct**: Filter by correlation_id to reconstruct incident timeline.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Correlation IDs**: Events that can't be linked to an order lifecycle.
- **Clock Skew**: Using wall-clock time without monotonic sequence numbers.
- **Log Volume Explosion**: Logging every tick without sampling or level gating.

## Verification

- Emit structured log events and verify JSON schema compliance.
- Query by correlation_id and verify complete timeline reconstruction.
- Run `python scripts/test_structured_logger.py` and confirm 100% pass rate.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `systemd-supervision-for-trading-bots`
---
