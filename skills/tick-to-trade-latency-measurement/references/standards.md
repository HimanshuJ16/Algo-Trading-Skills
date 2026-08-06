# Institutional Tick-to-Trade Latency Standards

## 1. Tick-to-Trade Pipeline Stage Definitions
| Stage | Microstructure Event | Start Marker ($t_{start}$) | End Marker ($t_{end}$) | Target Budget ($P_{50}$) | Target Budget ($P_{99}$) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Stage 1: NIC Ingress** | Network packet arrival -> User space read | Hardware NIC Timestamp ($t_0$) | Socket Buffer Read ($t_1$) | $< 300\ \text{ns}$ | $< 1.0\ \mu\text{s}$ |
| **Stage 2: Protocol Decoder** | Fast/SBE/ITCH decoding & L1 book update | Socket Buffer Read ($t_1$) | Internal Quote Event ($t_2$) | $< 500\ \text{ns}$ | $< 1.5\ \mu\text{s}$ |
| **Stage 3: Strategy / Alpha** | Pricing signal calculation & risk check | Internal Quote Event ($t_2$) | Order Signal Output ($t_3$) | $< 1.0\ \mu\text{s}$ | $< 3.0\ \mu\text{s}$ |
| **Stage 4: Order Serializer** | FIX / OUCH / BOE encoding | Order Signal Output ($t_3$) | Socket Write ($t_4$) | $< 400\ \text{ns}$ | $< 1.2\ \mu\text{s}$ |
| **Stage 5: NIC Egress** | Socket write -> Physical wire transmission | Socket Write ($t_4$) | Hardware Wire Timestamp ($t_5$) | $< 300\ \text{ns}$ | $< 1.0\ \mu\text{s}$ |
| **Total Tick-to-Trade** | End-to-End Execution Pipeline | Hardware Ingress ($t_0$) | Hardware Egress ($t_5$) | **$< 2.5\ \mu\text{s}$** | **$< 8.0\ \mu\text{s}$** |

## 2. Hardware vs. Software Timestamping Architecture
- **Hardware NIC Timestamping (Gold Standard)**: Solarflare EF_VI / AMD Onload or Mellanox Libvma captures hardware packet timestamps ($t_0$ and $t_5$) directly at the MAC/PHY layer via PTP IEEE 1588v2 hardware clock. Precision: **$< 10\ \text{ns}$**.
- **Kernel Bypass (OpenOnload / DPDK)**: Bypasses standard Linux OS TCP/IP stack (`sys_recvfrom`) to eliminate kernel context switches.
- **CPU TSC Counter (`rdtsc`)**: Used for internal C++/Python software stage profiling ($t_1, t_2, t_3, t_4$). Requires CPU core pinning (`taskset`) and disabling C-states / Turbo Boost to ensure constant TSC frequency.

## 3. Institutional SLA & Jitter Targets
- **Median ($P_{50}$)**: $\le 5.0\ \mu\text{s}$ (Colocated DMA / HFT Market Making).
- **Tail Latency ($P_{99}$)**: $\le 15.0\ \mu\text{s}$.
- **Extreme Tail ($P_{99.9}$)**: $\le 50.0\ \mu\text{s}$.
- **Jitter ($\sigma$)**: Standard deviation of T2T latency $\le 2.0\ \mu\text{s}$. High jitter indicates OS interrupt contention or CPU cache misses.
