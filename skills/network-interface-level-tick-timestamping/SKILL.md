---
name: network-interface-level-tick-timestamping
description: >-
  Use when operating low-latency feed handlers (Solarflare Onload, PTP hardware NICs, kernel-bypass) to extract hardware network interface packet timestamps (SO_TIMESTAMPING), bypassing OS kernel queueing jitter.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "nic-timestamping", "kernel-bypass", "solarflare", "hardware-clock", "so-timestamping", "low-latency"]
brokers_frameworks: ["NIC Hardware Timestamper", "Python Socket Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building sub-microsecond latency-sensitive feed handlers connected to direct exchange gateways. Application-level timestamping (`time.time()`) includes OS kernel network stack traversal, TCP buffer queueing, and context switching delays. Extracting hardware timestamps directly from the Network Interface Card (NIC) hardware clock (`SO_TIMESTAMPING` or Solarflare Onload API) provides true packet arrival time at the physical wire, eliminating kernel queueing jitter ($5\mu\text{s}$ to $500\mu\text{s}$).

## Prerequisites

- Network Interface Card supporting hardware PTP / packet timestamping (e.g. Solarflare SFN8522, Intel E810).
- OS Socket option `SO_TIMESTAMPING` or `SO_TIMESTAMPNS` enabled.

## Workflow

1. **Enable Socket Hardware Timestamping**:
   - Set socket options:
     ```python
     sock.setsockopt(socket.SOL_SOCKET, socket.SO_TIMESTAMPNS, 1)
     ```

2. **Extract Hardware Control Messages (Ancillary Data)**:
   - Use `recvmsg` to receive packet payload along with ancillary control data (`cmsg_level`, `cmsg_type`, `cmsg_data`).

3. **Decode Hardware Nanosecond Timestamp $T_{\text{hw}}$**:
   - Unpack struct `timespec` (seconds and nanoseconds) from `SO_TIMESTAMPNS` control buffer.

4. **Compute Kernel Queueing Jitter**:
   - Calculate $\Delta t_{\text{kernel}} = T_{\text{app\_rec}} - T_{\text{hw\_nic}}$. Attach $T_{\text{hw\_nic}}$ as the authoritative tick timestamp.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring PTP Clock Synchronization**: Using hardware NIC timestamps when the NIC clock is not synchronized to PTP grandmaster, causing clock drift relative to exchange time.
- **Falling Back to Software Timestamps Without Logging**: Silently falling back to `time.time()` when NIC driver fails without alerting operations.
- **Ancillary Data Buffer Truncation**: Setting `ancbufsize` too small in `recvmsg`, losing hardware timestamp control headers.

## Verification

- Simulate socket packet receipt with ancillary hardware timestamp header, verifying nanosecond extraction.
- Measure kernel queueing jitter $\Delta t_{\text{kernel}}$ and confirm $T_{\text{hw}}$ precedence.
- Run `python scripts/test_nic_timestamping.py` and confirm 100% pass rate.

## Related Skills

- `high-frequency-time-synchronization-ptp-ntp`
- `binary-protocol-parsing-for-low-latency-feeds`
- `feed-handler-cpu-pinning-and-numa-awareness`
---
