---
name: matching-engine-throttle-and-message-gapping-detection
description: >-
  Exchange matching engine monitoring engine detecting outbound order rate throttling (CME iLink 3 / NASDAQ OUCH) and inbound sequence gapping to trigger automatic resend recovery.
domain: Market Microstructure Latency
subdomain: Exchange Protocol Reliability & Session Governance
tags: ["matching-engine", "throttle-detection", "sequence-gapping", "cme-ilink3", "fixp", "nasdaq-ouch", "retransmit-request", "rate-limiting"]
brokers_frameworks: ["CME iLink 3 FIXP", "NASDAQ OUCH", "Binance API Rate Limits", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when connecting algorithmic trading engines to exchange matching engines (CME iLink 3, NASDAQ OUCH/ITCH, Binance API). Exchanges enforce strict **Messages-Per-Second (MPS)** limits (e.g. 500 msgs/sec per iLink session). Exceeding these limits results in business-level rejects or immediate session termination by the exchange. Simultaneously, network packet drops cause **Message Sequence Gapping**, requiring immediate detection and automated FIX Retransmit Requests.

## Prerequisites

- Outbound message log (`session_id`, `message_type`, `timestamp_epoch`, `sequence_id`).
- Inbound sequence monitor (`expected_seq_num`).
- Session rate limit specification (`max_mps_limit`: e.g. 500 msgs/sec, `warning_threshold_pct`: 80.0%).

## Workflow

1. **Outbound Order Rate Throttle Monitoring**:
   - Audit message count $N_{\text{msgs}}$ in sliding 1.0-second window.
   - If $N_{\text{msgs}} \ge 0.80 \times \text{max\_mps} \implies$ Trigger `THROTTLE_WARNING_SLOW_DOWN`.
   - If $N_{\text{msgs}} \ge \text{max\_mps} \implies$ Block outbound orders and trigger `EXCHANGE_RATE_LIMIT_THROTTLED`.
2. **Inbound Sequence Gap Detection**:
   - For each inbound execution report $i$, check `sequence_id` ($Seq_i$).
   - If $Seq_i > Seq_{\text{expected}} \implies$ Detect gap $\Delta = Seq_i - Seq_{\text{expected}}$, record missing range $[Seq_{\text{expected}}, Seq_i - 1]$, and trigger `MESSAGE_SEQUENCE_GAP_DETECTED`.
   - If $Seq_i == Seq_{\text{expected}} \implies$ Increment $Seq_{\text{expected}} = Seq_i + 1$.
3. **Audit Report Generation**: Output structured `MatchingEngineAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Triggering Exchange Session Disconnections**: Sending burst orders exceeding exchange MPS limits without client-side throttling, causing forced exchange disconnects.
- **Ignoring Sequence Gaps**: Processing out-of-order execution reports without issuing Retransmit Requests, corrupting local order state.
- **Infinite Resend Loops**: Requesting huge sequence gap replays ($> 2,500$ messages) repeatedly without backoff.

## Verification

- Instantiate `MatchingEngineMonitorEngine`. Simulate 450 outbound orders/sec (with max limit 500 msgs/sec) $\implies$ verify `THROTTLE_WARNING_SLOW_DOWN`. Simulate 550 orders/sec $\implies$ verify `EXCHANGE_RATE_LIMIT_THROTTLED`. Ingest inbound sequence ID 105 when expecting 101 $\implies$ verify `MESSAGE_SEQUENCE_GAP_DETECTED` for missing range [101, 104].
- Run `python scripts/test_matching_engine_throttle_and_message_gapping_detection.py`.

## Related Skills

- `exchange-gateway-redundancy-and-failover-testing`
- `message-rate-limit-vs-latency-tradeoff-tuning`
---
