---
name: exchange-multicast-feed-handling
description: >-
  Use when connecting to co-located exchange gateways (CME MDP 3.0, NASDAQ MoldUDP64) to handle dual A/B UDP multicast channels, re-sequence out-of-order packets, and issue TCP historical gap-fill re-transmission requests.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "udp-multicast", "cme-mdp", "moldudp64", "co-location", "packet-resequencing", "gap-fill"]
brokers_frameworks: ["Multicast Feed Handler", "Python Socket Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating co-located ultra-low-latency trading servers receiving raw UDP multicast feeds from derivative and equity exchanges (e.g., CME MDP 3.0 Channel A/B, NASDAQ MoldUDP64, Eurex ETI/EMDI). Unlike TCP/WebSockets, UDP multicast does not provide native delivery guarantees or re-transmission. This skill arbitrates between redundant Channel A and B multicast streams, re-sequences out-of-order packets, and requests missing sequence ranges from TCP historical re-transmission servers.

## Prerequisites

- Multicast IP groups and port numbers for Channel A and Channel B (e.g., `233.252.8.1:14001`).
- Exchange TCP historical re-transmission server endpoint.

## Workflow

1. **Bind Dual A/B Multicast Sockets**:
   - Join UDP multicast groups for Channel A and Channel B.

2. **Deduplicate & Re-Sequence Packets**:
   - Track expected sequence $S_{\text{expected}}$. If packet $S$ arrives on Channel A or Channel B, process immediately.
   - Buffer future out-of-order packets ($S > S_{\text{expected}}$).

3. **Issue TCP Historical Re-Transmission Request**:
   - If both Channel A and Channel B drop packet $S$ (sequence gap detected), send TCP gap request for missing range $[S_{\text{expected}}, S-1]$.

4. **Reconcile Gap & Drain Buffer**:
   - Ingest re-transmitted TCP packets and drain buffered multicast frames in sequential order.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Multicast Channel Dependency**: Reading only Channel A without joining Channel B, missing instantaneous packet recovery when Channel A drops a packet.
- **Unbounded Out-of-Order Buffer Growth**: Failing to cap the out-of-order packet queue during sustained network multicast drops.
- **Ignoring IGMP Group Joins**: Forgetting to issue IGMP `IP_ADD_MEMBERSHIP` socket options, preventing NIC hardware from receiving multicast traffic.

## Verification

- Simulate dual UDP Channel A/B packets (seq 100 on A, seq 101 on B) and verify seamless merged sequence stream.
- Simulate packet gap on both channels and verify TCP re-transmission request trigger.
- Run `python scripts/test_multicast_handler.py` and confirm 100% pass rate.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `sequence-number-gap-detection-for-feeds`
- `high-frequency-time-synchronization-ptp-ntp`
---
