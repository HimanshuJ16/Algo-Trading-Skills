---
name: capacity-planning-for-symbol-universe-growth
description: Quantitative infrastructure modeling tool to forecast CPU, memory, and
  network bandwidth requirements as a strategy's symbol universe expands.
domain: Infrastructure
subdomain: Scalability
tags:
- capacity-planning
- throughput
- bandwidth
- memory
- symbol-universe
brokers_frameworks:
- Generic Systems Engineering
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when preparing to scale a quantitative strategy from a small basket of symbols (e.g., 50 tech stocks) to a massive universe (e.g., Russell 3000 or global crypto pairs). The engine models the expected network bandwidth (Mbps), memory footprint (GB), and CPU core requirements based on peak market data tick rates, ensuring your infrastructure is scaled proactively to prevent latency spikes and dropped packets.

## When NOT to Use

- **As a substitute for measurement.** This is a first-order sizing model for a *procurement* decision. It does not model queueing delay distributions, NUMA effects, cache behaviour, GC pauses or kernel bypass. Before committing to a design, replay real captures at target rate.
- **When per-symbol rates are wildly skewed and you need a tight answer.** The model multiplies one peak rate by the symbol count. Real message rates follow a heavy-tailed distribution — a handful of names dominate — so a uniform peak applied across a 3,000-symbol universe overestimates aggregate load, while a uniform *average* underestimates the hot symbols that actually break your feed handler. See Common Pitfalls.
- **For latency budgeting.** Capacity and latency are different questions; use `strategy-latency-budget-decomposition` and `tick-to-trade-latency-measurement`.

## Prerequisites

- Base knowledge of average and peak tick rates (messages per second) for the target asset class, measured over the **burst window you actually care about** (see `peak_rate_per_sec_from_burst`).
- Specifications of the hardware: NIC bandwidth, cores you can genuinely dedicate to feed processing, RAM, and measured messages/second/core for your parser.
- Knowledge of your feed's transport: whether messages are batched into packets, and whether you take one feed line or a redundant A/B pair.

## Units

Mixed by design, matching how each domain is quoted commercially:
- **Network is decimal** — 1 Mbps = 10⁶ bits/sec, so a "10 GbE" NIC is `nic_bandwidth_mbps=10000`.
- **Memory is binary** — 1 GB = 1024 MB (GiB/MiB), matching how RAM is sold and reported.

## Workflow

1. **Parameter Initialization**: Define `HardwareSpec` — NIC bandwidth, measured `cpu_msgs_per_sec_per_core`, RAM, and `available_cpu_cores`. Set `available_cpu_cores` to cores you can *dedicate*, not the machine's total; the OS and your strategy threads need their own.
2. **Universe Definition**: Define `UniverseSpec`. Decide how you are charging wire overhead, and be consistent:
   - *Unbatched:* fold payload + framing into `bytes_per_msg`, leave `msgs_per_packet=1`, `packet_overhead_bytes=0`.
   - *Batched (correct for ITCH over MoldUDP64 and similar):* put only the payload in `bytes_per_msg`, set `packet_overhead_bytes=UDP_IPV4_ETHERNET_OVERHEAD_BYTES` (66) and `msgs_per_packet` to your observed batching factor.
3. **Account for Feed Reality**: Set `redundant_feeds=2` if you consume both sides of an A/B multicast pair — it genuinely doubles the bill. Set `retransmission_overhead_fraction` for gap-fill traffic; OPRA's own guidance is +10%.
4. **Choose a Margin Deliberately**: `safety_margin` defaults to `1.0` (no inflation) so the model reports exactly what you specified. Raise it to cover forecast uncertainty, but do not silently double-count against `max_network_utilization`, which already reserves headroom on the supply side.
5. **Capacity Calculation**: `CapacityPlanner.evaluate(universe)` returns bandwidth, RAM, core count, each bottleneck flag, and the utilisation ceiling it applied.
6. **Validation — read all the flags, not just `is_viable`**: If `single_symbol_exceeds_core` is set, the design is infeasible *at any core count* under symbol partitioning; adding hardware will not fix it, and you must either split that symbol's processing by message type or move to a faster parser. If only `network_bottleneck` is set, a bigger NIC or a filtered subscription may suffice.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Designing for Average Load**: Architecting for the *average* daily tick rate instead of the *peak* microburst. OPRA publishes its capacity projections in messages per 100ms and per 10ms intervals precisely because the 10ms window "reflects system utilization during bursts of traffic" — a one-second average can hide a burst an order of magnitude larger. Use `peak_rate_per_sec_from_burst(messages, window_ms)` to convert a burst observation into the rate to plan against.
- **Charging Per-Packet Framing to Every Message**: The 66 bytes of UDP/IPv4/Ethernet framing is paid **per packet**, not per message. MoldUDP64 batches many ITCH messages into a single datagram (max 1472 bytes), so billing every message for full framing can overstate required bandwidth several-fold and send you shopping for a link you do not need.
- **Forgetting the B Feed**: Redundant A/B multicast lines double bandwidth. OPRA states its projections are "for one stream only" and that subscribers taking both streams need double the bandwidth. A model that silently assumes one line under-provisions by 100%.
- **Assuming Cores Are Fungible**: A single symbol's stream cannot be split across cores when you partition by symbol. If the hottest symbol exceeds one core's throughput, a 64-core machine does not help — this is what `single_symbol_exceeds_core` catches, and a raw "required cores" figure hides it entirely.
- **Uniform Per-Symbol Rates**: Multiplying one peak rate by 3,000 symbols assumes every symbol is as hot as your benchmark. Message rates are heavy-tailed; model your universe in liquidity tiers and sum them rather than trusting a single uniform figure.
- **Single-Threaded Bottlenecks**: Assuming that buying a 64-core machine solves the problem, without writing partitioned, lock-free code that can actually utilize those cores.

## Verification

- Reproducible worked example, pinned by the test suite: a 5,000-symbol universe at 1,000 msgs/sec/symbol and 125 bytes/msg needs 5,000 Mbps — 5 Gbps — which a 1 Gbps link cannot carry (`network_bottleneck=True`, `is_viable=False`).
- Confirm the batching correction: the same 1M msgs/sec at 50-byte payloads costs 928 Mbps charged per message versus 426.4 Mbps when batched 20 messages per packet.
- Confirm a 20-core requirement is flagged on a 16-core `HardwareSpec` — earlier versions compared against a hard-coded 64.
- Run `python -m unittest discover -s skills/capacity-planning-for-symbol-universe-growth/scripts`.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `broker-side-order-throttle-detection`
- `tick-buffering-burst-handling`
- `load-testing-before-scaling-to-new-instrument-universe`
