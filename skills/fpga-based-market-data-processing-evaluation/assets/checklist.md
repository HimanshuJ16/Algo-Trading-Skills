# Pre-Flight Checklist — FPGA Acceleration Capital Decision

## Measurement

- [ ] Both latency profiles declare the **same** `measurement_basis`, and neither is `VENDOR_COMPONENT`.
- [ ] No figure in either profile was taken from vendor collateral, a datasheet, or a benchmark press release.
- [ ] Latency was captured by hardware timestamping off a passive tap — not from business clocks (RTS 25 allows 1 µs granularity, ~1,000× too coarse here).
- [ ] The measured topology is the one that would actually be deployed. If the host makes the decision, the PCIe DMA round trip (585–790 ns) is inside the measurement.
- [ ] `sample_count` is recorded for both profiles and is ≥ 100 (the floor for a p99 to exist) — ideally orders of magnitude more.
- [ ] Percentiles satisfy p50 ≤ p99 ≤ max on both sides.

## Latency

- [ ] Median reduction ΔL computed, and checked against the house threshold (default ≥ 1,000 ns — policy, not a standard).
- [ ] Worst-case (max) reduction reported alongside the median; determinism is most of the FPGA case.
- [ ] Tail-spread ratio computed, and a zero FPGA spread investigated as a sampling artefact rather than accepted as determinism.

## Economics

- [ ] Every cost line is classified as one-time or recurring, and each licence appears in exactly one field.
- [ ] `evaluation_horizon_years` reflects the card's expected service life before refresh.
- [ ] Capex is amortised — the annual alpha gain is compared against the **annualised** cost, never against a capex-plus-opex lump sum.
- [ ] Horizon TCO and payback period are reported, not just the annual net.
- [ ] Ongoing HDL/firmware engineering is budgeted, including the cost of tracking venue protocol changes.
- [ ] The resulting cash flows have been run through a proper NPV model (this engine does not discount).

## Alpha

- [ ] `estimated_annual_alpha_gain_usd` has an independent derivation — it is an input, not an engine output.
- [ ] `alpha_half_life_ns` supplied where known, and the reported uplift factor is consistent with the claimed gain.
- [ ] For a low-turnover strategy, the contradiction has been resolved rather than overridden.

## Sign-off

- [ ] `data_quality_flags` from the report reviewed line by line with the approving committee.
- [ ] Any `INSUFFICIENT_EVIDENCE` verdict was resolved by fixing the measurement or the estimate — not by relabelling a profile.
