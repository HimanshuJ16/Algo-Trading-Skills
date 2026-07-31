---
name: hardware-timestamping-vs-software-timestamping-accuracy
description: >-
  Low-latency benchmarking engine for measuring Hardware NIC (Solarflare/Exablaze) vs Software OS Kernel timestamping jitter and auditing MiFID II RTS 25 compliance (< 100us UTC drift).
domain: Market Microstructure & Latency
subdomain: Hardware NIC Timestamping & MiFID II Compliance
tags: ["hardware-timestamping", "solarflare", "mifid-ii", "rts-25", "kernel-jitter", "nanosecond-precision", "latency-benchmarking"]
brokers_frameworks: ["Solarflare OpenOnload / EF_VI", "PTP IEEE 1588", "Linux SO_TIMESTAMPING", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in high-frequency trading (HFT) co-located execution gateways, market data tick capture systems, and regulatory audit compliance engines. Software timestamps (captured in user-space or kernel `SO_TIMESTAMPING`) suffer from CPU context-switching, interrupt delays, and kernel queuing jitter ($1\mu\text{s} - 50\mu\text{s}$ error drift). Hardware timestamping (captured at MAC/PHY layer on PCIe SmartNICs like Solarflare) achieves nanosecond-level accuracy ($< 10\text{ns}$). This module decomposes timestamp latency layers and audits MiFID II RTS 25 compliance ($100\mu\text{s}$ max divergence from UTC).

## Prerequisites

- Multi-layer packet timestamps ($T_{\text{hw\_nanos}}$, $T_{\text{kernel\_nanos}}$, $T_{\text{app\_nanos}}$, $T_{\text{utc\_ref\_nanos}}$).
- MiFID II RTS 25 compliance threshold ($100,000\text{ ns} = 100\mu\text{s}$).

## Workflow

1. **Multi-Layer Timestamp Ingestion**:
   - Ingest hardware MAC timestamp ($T_{\text{hw}}$), kernel timestamp ($T_{\text{kernel}}$), and application timestamp ($T_{\text{app}}$).
2. **Latency Decomposition Calculation**:
   - Kernel Stack Latency: $\Delta t_{\text{kernel}} = T_{\text{kernel}} - T_{\text{hw}}$.
   - Application Jitter: $\Delta t_{\text{app}} = T_{\text{app}} - T_{\text{kernel}}$.
   - Software Distortion: $\Delta t_{\text{total\_sw}} = T_{\text{app}} - T_{\text{hw}}$.
3. **MiFID II RTS 25 Compliance Audit**:
   - Evaluate Hardware UTC Drift: $|T_{\text{hw}} - T_{\text{UTC\_ref}}| \le 100,000\text{ ns}$.
   - Evaluate Software UTC Drift: $|T_{\text{app}} - T_{\text{UTC\_ref}}| \le 100,000\text{ ns}$.
4. **Audit Report Generation**: Output structured `TimestampAccuracyAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on User-Space Software Timestamps for HFT Audits**: Using `clock_gettime(CLOCK_REALTIME)` in application code for MiFID II audit logs, failing regulatory audits during CPU spike events.
- **Ignoring PTP Hardware Clock Drift**: Operating hardware NIC timestamping without continuous PTP IEEE 1588 grandmaster clock synchronization.
- **Conflating Kernel Timestamping with Hardware Timestamping**: Assuming Linux `SO_TIMESTAMPING` software fallback equals hardware MAC-layer capture.

## Verification

- Instantiate `TimestampAccuracyAnalyzerEngine`. Input Hardware MAC timestamp ($T_{\text{hw}} = 100,000\text{ ns}$ drift), Kernel ($+5,000\text{ ns}$), App ($+120,000\text{ ns}$). Verify engine calculates Software Jitter $= 125\text{ µs}$, flags Software as `NON_COMPLIANT_RTS_25`, and confirms Hardware is `COMPLIANT_RTS_25`.
- Run `python scripts/test_hardware_timestamping_vs_software_timestamping_accuracy.py`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `hardware-timestamping-vs-software-timestamping-accuracy`
---
