---
name: fpga-based-market-data-processing-evaluation
description: >-
  Use when deciding whether to buy an FPGA SmartNIC tick-to-trade path instead of continuing to optimise a kernel-bypass CPU path: checks that the two latency profiles were measured on the same basis, amortises hardware and firmware capex before comparing it with annual alpha, and tests the claimed alpha gain against the strategy's own decay half-life.
domain: Latency & Hardware Acceleration
subdomain: FPGA Market Data & Ultra-Low-Latency Architecture
tags: ["fpga", "hft", "market-data-parsing", "tick-to-trade", "nanosecond-latency", "smartnic", "hardware-acceleration", "stac-t0"]
brokers_frameworks: ["AMD Alveo UL3524", "AMD Solarflare EF_VI / Onload", "Enyx FPGA", "STAC-T0", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a desk is deciding between **continuing to optimise a kernel-bypass CPU path** (EF_VI/Onload, DPDK) and **buying an FPGA SmartNIC path** where the feed is parsed and the order emitted inside the device.

Order-of-magnitude context, with the caveat that every number below is quoted under a *different measurement definition* (see the first pitfall):

- **Software, kernel bypass.** Databento's microstructure guide puts an optimised software tick-to-trade "just under 2 microseconds" on a Solarflare X2522 with TCP checksum offload and OpenOnload. The p50 is the easy part; the tail is set by scheduling, cache misses and interrupt coalescing, and is not bounded by anything you control.
- **FPGA.** Sub-microsecond is routine. Published STAC-T0 results include 98–120 ns for the LDA/Solarflare/Xilinx network-I/O stack, and 13.9 ns *actionable latency* for the Exegy/AMD Alveo UL3524 solution. AMD separately publishes "<3 ns transceiver latency" for the same card — a component figure, not a path.

The engine turns that comparison into a defensible capital decision. It computes the p50 and worst-case reductions and the tail-spread ratio, amortises non-recurring cost over an evaluation horizon before setting it against annual alpha, and returns `FPGA_RECOMMENDED`, `SOFTWARE_CPU_SUFFICIENT`, or `INSUFFICIENT_EVIDENCE`.

## When NOT to Use

- **As an alpha model.** `estimated_annual_alpha_gain_usd` is an input, not an output. The engine never derives how much money a latency reduction is worth; it only checks that your number does not contradict your own stated alpha half-life.
- **When both latency profiles are not measured the same way.** This is the normal case with vendor collateral, and the engine will refuse the comparison rather than produce a number. Fix the measurement first.
- **When the FPGA parses but the host decides.** Best published FPGA-to-host PCIe DMA round trips are 585 ns (Orthogone ULL DMA, Gen4 x8) and 790 ns (DMA Calypte, Gen3 x8) — one to two orders of magnitude above the FPGA's internal figure. A partial offload is a different architecture with a different latency profile; measure *that* topology end to end and feed the result in, rather than reasoning from the card's spec sheet.
- **As a substitute for STAC-T0 or an equivalent benchmark.** This engine consumes latency distributions. It does not produce them, and it has no view on whether yours were measured competently.
- **For strategies whose edge does not decay on a nanosecond scale.** A 5-minute alpha half-life makes a 2,250 ns saving worth a ~5×10⁻⁹ relative improvement in retained edge. The engine flags this, but the honest answer is that the question should not have reached a capital committee.
- **As a full capital-budgeting model.** Straight-line amortisation, no discounting, no tax shield, no residual value. Run the resulting cash flows through a proper NPV model before signing.

## Prerequisites

- **Two latency distributions measured on the same declared basis**, one per topology: p50, p99, max and jitter standard deviation in nanoseconds, plus the sample count behind them. `measurement_basis` is a required field with no default — one of `WIRE_TO_WIRE`, `STAC_T0_ACTIONABLE`, `APPLICATION_INTERNAL`, `VENDOR_COMPONENT`.
- **Hardware timestamping off a passive tap.** Business-clock timestamps are not adequate at this resolution: MiFID II RTS 25 (Commission Delegated Regulation (EU) 2017/574, Annex Table 2) requires only 100 µs maximum divergence from UTC and 1 µs granularity even for HFT — three orders of magnitude coarser than the deltas being priced here. Regulatory compliance says nothing about whether your latency measurement is usable.
- **At least 100 observations per profile** for a p99 to exist at all; orders of magnitude more for it to be stable. Supply `sample_count` so the engine can flag the arithmetic floor.
- **Costs split by recurrence**: one-time (card, perpetual IP core licence, initial HDL engineering) versus recurring (annual firmware maintenance, annual IP subscription). Put each licence in exactly one field.
- **Annual incremental alpha attributed to the latency reduction**, and, where known, the strategy's **alpha decay half-life in nanoseconds**. Pass `None` for the half-life if you do not know it — the engine skips the consistency check rather than assuming a value.

## Workflow

1. **Comparability Gate** — before any arithmetic, compare the two `measurement_basis` values.
   - Either side is `VENDOR_COMPONENT`, or the two differ ⟹ `INSUFFICIENT_EVIDENCE`. The delta is still reported, labelled as arithmetic rather than as a saving. Do not treat this as a soft warning: re-benchmark both paths on one basis.
   - Both `APPLICATION_INTERNAL` ⟹ proceed, flagged. In-process timestamps exclude NIC, driver and wire time on both sides, and exclude proportionally more of the software path, so the delta is understated in the FPGA's favour on the parts you did measure and unmeasured elsewhere.
2. **Latency Profile Audit**:
   - $\Delta L = L_{\text{cpu,p50}} - L_{\text{fpga,p50}}$, and the worst-case reduction $L_{\text{cpu,max}} - L_{\text{fpga,max}}$. A negative $\Delta L$ is flagged, not silently carried.
   - Tail spread ratio $= \frac{L_{\text{cpu,p99}} - L_{\text{cpu,p50}}}{L_{\text{fpga,p99}} - L_{\text{fpga,p50}}}$. Profiles are validated for $p50 \le p99 \le \max$, so both spreads are non-negative; a zero FPGA spread yields `inf` and a flag, because in practice it means too few samples rather than perfect determinism.
3. **Cost of Ownership** — separate the stock from the flow:
   - $\text{Capex} = \text{card} + \text{perpetual IP licence} + \text{one-time HDL NRE}$; $\text{Recurring} = \text{annual maintenance} + \text{annual IP subscription}$.
   - $\text{TCO}_{\text{horizon}} = \text{Capex} + \text{Recurring} \times H$; $\text{Cost}_{\text{annualised}} = \frac{\text{Capex}}{H} + \text{Recurring}$.
   - Only $\text{Cost}_{\text{annualised}}$ may be set against an annual alpha figure. Payback $= \text{Capex} / (\text{Gain} - \text{Recurring})$, reported as `None` when that surplus is not positive.
4. **Alpha Consistency Check** — where a half-life $T$ is supplied, the retained-edge uplift is $2^{\Delta L / T}$ under an assumed exponential decay. If that uplift is below the materiality threshold (default $1.01$) while a positive alpha gain is claimed, the two inputs contradict each other ⟹ `INSUFFICIENT_EVIDENCE`. This is a consistency test between two caller-supplied numbers, not the engine overriding your alpha estimate; set `enforce_alpha_decay_consistency=False` to demote it to a flag.
5. **Decision**: $\Delta L \ge$ `min_latency_reduction_threshold_ns` **and** net annual ROI $>$ `min_roi_net_benefit_usd` ⟹ `FPGA_RECOMMENDED`; otherwise `SOFTWARE_CPU_SUFFICIENT`. Both thresholds are house policy, not standards — the defaults are 1,000 ns and a zero margin of safety.
6. **Audit Report**: output a structured `FpgaEvaluationReport` carrying every intermediate figure and the `data_quality_flags` list, so the committee sees what was flagged and not just the verdict.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Subtracting latencies measured under different definitions.** The Exegy/AMD 13.9 ns STAC-T0 record is *actionable latency*: the clock starts when the triggering field (a price) enters the FPGA, not at the start of the UDP frame. AMD's "<3 ns" is transceiver latency. Your software number is almost certainly wire-to-wire. Subtracting a vendor's number from your own does not produce a saving; it produces an arithmetic result that happens to justify a six-figure purchase. The engine blocks this; the failure mode is bypassing it by mislabelling a profile.
- **Charging capex as if it recurred annually.** Adding a $15,000 card and a $25,000 perpetual IP licence to $20,000/yr of firmware maintenance gives $60,000 — a number that is neither an annual cost nor a total. Over a three-year horizon the same inputs are a $100,000 TCO and a $33,333/yr annualised cost. Compared against a $45,000/yr alpha gain, the first arithmetic rejects a build that the second correctly approves.
- **Overestimating alpha gain for low-turnover strategies.** Spending six figures on hardware for a strategy with a five-minute holding period, where a microsecond buys nothing. Supply `alpha_half_life_ns` and let the engine surface the contradiction rather than arguing it in a meeting.
- **Ignoring VHDL/Verilog firmware maintenance.** Budgeting the card and the IP core while treating HDL engineering as free. Exchange protocol changes land on the firmware team, and an FPGA feed handler that lags a venue's spec update is worse than a software one that does not.
- **Accelerating the parse while leaving the decision on the host.** The PCIe DMA round trip (585–790 ns in the best published implementations) exceeds the entire FPGA-internal figure. If the host is in the loop, the card's spec sheet is not describing your system.
- **Reading a p99 off too few samples.** The 99th percentile of 50 observations is the maximum wearing a different name — and a zero measured tail spread is a sampling artefact, not determinism.
- **Treating RTS 25 clock-sync compliance as measurement capability.** 1 µs granularity is compliant and useless here.

## Verification

- Instantiate `FpgaMarketDataEvaluationEngine()` (defaults: 1,000 ns latency floor, zero ROI margin, 3-year horizon). Feed a software profile ($p50 = 2{,}500$ ns, $p99 = 8{,}000$ ns, $\max = 15{,}000$ ns) and an FPGA profile ($p50 = 250$ ns, $p99 = 300$ ns, $\max = 450$ ns), **both** on `WIRE_TO_WIRE`, with costs of $15{,}000 + \$25{,}000$ capex and \$20{,}000/yr recurring against a \$250,000/yr alpha gain and a 3,000 ns half-life. Expect `FPGA_RECOMMENDED`, $\Delta L = 2{,}250$ ns, worst-case reduction $14{,}550$ ns, tail ratio $110.0$, horizon TCO \$100,000, annualised cost \$33,333.33, net annual ROI \$216,666.67, payback 0.1739 years.
- Re-run with the FPGA profile relabelled `STAC_T0_ACTIONABLE` ⟹ `INSUFFICIENT_EVIDENCE` with `MEASUREMENT_BASIS_MISMATCH`.
- Re-run the original with `alpha_half_life_ns = 3e11` (five minutes) ⟹ `INSUFFICIENT_EVIDENCE` with `ALPHA_GAIN_INCONSISTENT_WITH_DECAY_HALF_LIFE` and an uplift factor of ~1.000000005.
- Run `python -m unittest discover -s skills/fpga-based-market-data-processing-evaluation/scripts`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `feed-handler-cpu-pinning-and-numa-awareness`
- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `colocation-latency-budget-accounting`
