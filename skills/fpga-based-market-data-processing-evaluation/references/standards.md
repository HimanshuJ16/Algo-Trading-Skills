# Standards & Sources for FPGA Market Data Processing Evaluation

## What is an actual standard, and what is house policy

There is **no industry or regulatory standard** that mandates a tick-to-trade
latency target, a minimum latency saving, or an ROI hurdle for an FPGA build.
Any such number in this skill is a configurable default, and is labelled as one.
What *is* standardised is how the latency should be measured.

| Item | Status | Source |
|---|---|---|
| STAC-T0 tick-to-trade network-I/O benchmark | Industry benchmark specification, published by the STAC Benchmark Council (300+ financial institutions, 50+ vendors) | https://stacresearch.com/benchmarks/ |
| STAC-T0 "actionable latency" definition | Benchmark-defined metric: the clock starts when the triggering field (e.g. a price) enters the device, **not** at the start of the inbound UDP frame | https://docs.stacresearch.com/news/AMD240422 |
| Business-clock accuracy for HFT: 100 µs max divergence from UTC, 1 µs timestamp granularity | **Mandatory** in the EU for trading-venue members using HFT techniques | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2 |
| `min_latency_reduction_threshold_ns` (default 1,000 ns) | House policy, configurable | This skill |
| `min_roi_net_benefit_usd` (default 0.0) | House policy, configurable; 0.0 encodes "annual alpha gain must exceed annualised TCO" | This skill |
| `evaluation_horizon_years` (default 3.0) | House policy, configurable; set it to the card's expected service life before refresh | This skill |
| `min_alpha_uplift_for_material_gain` (default 1.01) | Heuristic materiality threshold on a *modelled* quantity, configurable | This skill |

## Published latency reference points

These are the figures the skill's guidance is calibrated against. Note that each
is quoted under a different measurement definition and they are **not mutually
comparable** — that incomparability is the point.

| Figure | What it measures | Source |
|---|---|---|
| Just under 2 µs | Optimised **software** tick-to-trade, kernel bypass (Solarflare X2522, TCP checksum offload, OpenOnload) | https://databento.com/microstructure/tick-to-trade |
| Sub-microsecond, typical | FPGA/ASIC tick-to-trade generally; competitive firms quoted at single- to double-digit ns as of 2024 | https://databento.com/microstructure/tick-to-trade |
| 120 ns, then 98 ns | STAC-T0 **network I/O** for the Solarflare/LDA/Xilinx Kintex UltraScale stack | https://www.tradersmagazine.com/departments/technology/solarflare-and-lda-hit-120ns-cme-tick-to-trade-latency/ |
| 13.9 ns, 200 ps jitter | STAC-T0 **actionable latency**, Exegy/AMD on the Alveo UL3524 — triggering field in to TCP order out | https://a-teaminsight.com/blog/exegy-and-amd-achieve-record-breaking-tick-to-trade-latency-in-stac-t0-benchmark/ |
| < 3 ns | Alveo UL3524 **transceiver** latency — a component figure, not a path | https://www.xilinx.com/content/dam/xilinx/publications/product-briefs/2233051_Product_Brief_UL3524_Alveo_Accelerator_Card.pdf |
| 585 ns (Gen4 x8), 790 ns (Gen3 x8) | FPGA↔host **PCIe DMA round trip**, best published ultra-low-latency implementations | https://orthogone.com/ultra-low-latency-ethernet-mac/high-speed-data-transfer-smartnics-host-cpu-pcie-dma-controller/ · DMA Calypte (FPL 2025), https://www.computer.org/csdl/proceedings-article/fpl/2025/915300a291/2fazTC2ETMA |

## Feed protocols the FPGA path has to terminate

Market data parsing is an **application-layer** operation, not a Layer 2/3 one.
Nasdaq TotalView-ITCH for low-latency consumers is carried in MoldUDP64 over UDP
multicast, so an FPGA feed handler terminates Ethernet, IP and UDP, then the
MoldUDP64 framing, and only then decodes ITCH messages — all within the same
pipeline. CME MDP 3.0 similarly carries Simple Binary Encoding over a dual UDP
multicast feed requiring arbitration.

- Nasdaq TotalView-ITCH 5.0 specification: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification_5.0.pdf

## Engineering guidance derived from the above

| Guidance | Basis |
|---|---|
| Both latency profiles MUST declare and share a measurement basis before any delta is priced | Direct consequence of the incomparability of the figures tabulated above |
| A vendor component figure MUST NOT be used as a tick-to-trade profile | AMD publishes transceiver latency and STAC-T0 actionable latency as distinct metrics |
| Latency measurement SHOULD use hardware timestamping off a passive tap | RTS 25's 1 µs granularity is ~3 orders of magnitude too coarse for the deltas being evaluated |
| A p99 requires at least 100 observations to be defined, and far more to be stable | Arithmetic |
| Non-recurring capital MUST be amortised before comparison with an annual alpha figure | Standard capital budgeting; a stock cannot be subtracted from a flow |
| A host-in-the-loop design MUST have the PCIe round trip included in its measured profile | 585–790 ns exceeds the entire FPGA-internal latency figure |
