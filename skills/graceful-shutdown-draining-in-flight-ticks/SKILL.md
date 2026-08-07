---
name: graceful-shutdown-draining-in-flight-ticks
description: Use when shutting down or redeploying trading microservices (Kubernetes
  SIGTERM, systemd restart) to trap termination signals, stop new tick ingestion,
  drain in-flight queues, flush database sinks, and commit consumer offsets.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- graceful-shutdown
- sigterm
- queue-drain
- data-loss-prevention
- deployment-safety
brokers_frameworks:
- Graceful Shutdown Manager
- Python Signal Handler
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating live trading engines and market data workers subject to planned deployments, rolling restarts, or Kubernetes container terminations (`SIGTERM`). Abruptly killing process worker threads drops in-flight tick events, leaves database batch writes incomplete, and causes uncommitted message consumer offsets. This skill traps OS termination signals, closes ingress streams, drains queued ticks to completion, and exits cleanly.

## Prerequisites

- Ingestion pipeline with queue worker threads or async event loops.
- Max drain timeout $T_{\text{max\_drain}}$ (e.g. 5.0 seconds).

## Workflow

1. **Register OS Signal Traps**:
   - Intercept `SIGINT` (Ctrl+C) and `SIGTERM` (K8s/Docker shutdown) signals.

2. **Close External Ingress Streams**:
   - Transition shutdown state to `DRAINING`. Reject new incoming external network ticks.

3. **Drain In-Flight Queue Buffers**:
   - Process remaining $N$ items currently residing in worker queues until queue count reaches 0.

4. **Flush Sinks & Commit Offsets**:
   - Invoke downstream DB flush and commit stream offsets.

5. **Clean Process Exit**:
   - Terminate worker threads safely and exit process with status code 0.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unbounded Drain Waiting**: Waiting infinitely for stuck worker threads without a maximum drain timeout, causing K8s `SIGKILL` hard kills.
- **Accepting Ticks During Drain**: Failing to close network ingress sockets while draining, allowing new ticks to pile up indefinitely.
- **Ignoring Database Flush Failures**: Exiting process without catching exceptions thrown during final batch database flush.

## Verification

- Enqueue 50 items, trigger simulated `SIGTERM`, verify 50 items are drained to downstream callback before exit.
- Verify zero item loss during graceful shutdown.
- Run `python scripts/test_graceful_shutdown.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `adaptive-batch-size-tuning-under-load`
- `structured-logging-for-post-incident-forensics`
---
