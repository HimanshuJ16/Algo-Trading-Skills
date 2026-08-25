# Workflows for FPGA Market Data Processing Evaluation

## 0. Establish a comparable measurement basis (do this first)

Nothing downstream is meaningful until both topologies are measured the same
way. Pick one basis and hold it:

| Basis | Definition | When it is legitimate |
|---|---|---|
| `WIRE_TO_WIRE` | Last bit of the inbound market-data frame on the wire to the first bit of the outbound order frame, hardware-timestamped **outside** the system under test | The default and the only basis that captures the whole path, PCIe included |
| `STAC_T0_ACTIONABLE` | STAC-T0 actionable latency — the clock starts when the triggering field enters the device, so inbound framing is excluded | Comparing two STAC-T0 results with each other |
| `APPLICATION_INTERNAL` | In-process software timestamps only | Rough internal profiling; excludes NIC, driver and wire on both sides, and excludes proportionally more of the software path |
| `VENDOR_COMPONENT` | A transceiver, MAC or IP-core figure from a datasheet | Never, as a tick-to-trade profile. Present in the vocabulary so a mislabelled input can be rejected rather than silently priced |

Measure with hardware timestamping off a passive tap. MiFID II RTS 25 business
clocks (100 µs divergence, 1 µs granularity for HFT) are three orders of
magnitude too coarse to resolve the deltas under evaluation; compliance with
RTS 25 is not evidence that a latency measurement is usable here.

Capture enough messages for the percentiles to mean something. A p99 does not
exist below 100 observations, and a stable p99 needs far more. Record the
sample count alongside the percentiles and pass it in.

## 1. Latency profile benchmarking

Ingest p50, p99, max and jitter standard deviation for both topologies, in
nanoseconds, together with the declared basis and sample count. The profile
rejects non-finite or negative values and enforces `p50 <= p99 <= max` — an
inverted transcription silently inverts the tail-spread ratio, so it is treated
as an error rather than absorbed.

Measure the topology you would actually deploy. If the FPGA parses the feed but
the host makes the decision, the measurement must include the PCIe DMA round
trip (585 ns Gen4 x8, 790 ns Gen3 x8 in the best published implementations) —
which on its own exceeds the FPGA-internal figure by an order of magnitude.

## 2. Jitter and tail latency

- $\Delta L = L_{\text{cpu,p50}} - L_{\text{fpga,p50}}$; a negative value is
  flagged `FPGA_SLOWER_THAN_SOFTWARE_AT_P50` rather than carried silently.
- Worst-case reduction $= L_{\text{cpu,max}} - L_{\text{fpga,max}}$. Determinism
  is most of the FPGA thesis, so the worst case is reported alongside the median.
- Tail spread ratio $= \dfrac{p99_{\text{cpu}} - p50_{\text{cpu}}}{p99_{\text{fpga}} - p50_{\text{fpga}}}$.
  A zero FPGA spread yields `inf` plus `FPGA_TAIL_SPREAD_ZERO`; it almost always
  indicates too few samples rather than a perfectly deterministic device.

## 3. Cost of ownership

Split every line item by recurrence before adding anything up:

- **One-time**: SmartNIC card, perpetual IP-core licence, initial HDL
  engineering / integration NRE.
- **Recurring**: annual firmware maintenance, annual IP-core subscription where
  the vendor bills that way. Put each licence in exactly one field.

Then:

- $\text{TCO}_{\text{horizon}} = \text{Capex} + \text{Recurring} \times H$
- $\text{Cost}_{\text{annualised}} = \text{Capex}/H + \text{Recurring}$
- $\text{Payback} = \text{Capex} / (\text{Gain}_{\text{annual}} - \text{Recurring})$, undefined when that surplus is not positive.

Only $\text{Cost}_{\text{annualised}}$ may be compared with an annual alpha
figure. $H$ defaults to 3 years; set it to the card's expected service life
before refresh. Amortisation is straight-line with no discounting, tax shield or
residual value — run the cash flows through an NPV model before signing.

## 4. Alpha consistency

Supply the strategy's alpha decay half-life in nanoseconds where it is known.
Assuming edge decays exponentially in latency, retained edge at latency $L$ is
$2^{-L/T}$, so the reduction multiplies retained edge by $2^{\Delta L / T}$.

This is used **only** as a consistency check between two numbers you supplied.
If the uplift is below the materiality threshold (default 1.01) while a positive
alpha gain is claimed, the two inputs contradict each other and the engine
returns `INSUFFICIENT_EVIDENCE`. Worked example: a five-minute half-life
($3\times10^{11}$ ns) against a 2,250 ns saving gives an uplift of
$2^{7.5\times10^{-9}} \approx 1.0000000052$ — a claimed six-figure annual gain
is not consistent with that.

Pass `None` when the half-life is unknown; the check is skipped rather than
assumed away. Set `enforce_alpha_decay_consistency=False` to demote the check to
a flag without blocking.

## 5. Architecture selection

`FPGA_RECOMMENDED` requires both gates to pass and no blocking flag:

1. $\Delta L \ge$ `min_latency_reduction_threshold_ns` (default 1,000 ns);
2. net annual ROI $>$ `min_roi_net_benefit_usd` (default 0.0, i.e. the gain must
   strictly exceed the annualised cost).

`INSUFFICIENT_EVIDENCE` — never a spend recommendation — is returned when the
bases are mismatched, when either side is a vendor component figure, or when the
alpha claim contradicts the stated half-life.

Otherwise `SOFTWARE_CPU_SUFFICIENT`: continue on DPDK/EF_VI kernel-bypass
optimisation, CPU pinning and NUMA placement, which are cheaper and reversible.

## 6. Report

`FpgaEvaluationReport` carries every intermediate figure plus
`data_quality_flags`. Present the flags to the approving committee alongside the
verdict — a `FPGA_RECOMMENDED` carrying `INSUFFICIENT_SAMPLES_FOR_P99` or
`APPLICATION_INTERNAL_BASIS_EXCLUDES_NIC_AND_WIRE` is a weaker result than the
verdict alone suggests.
