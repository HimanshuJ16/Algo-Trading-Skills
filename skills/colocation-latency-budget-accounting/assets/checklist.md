# Pre-Flight Checklist

## Timestamp acquisition

- [ ] Are NIC **hardware** timestamps used for $T_0$ (ingress) and $T_5$ (egress)? (`ethtool -T <iface>` confirms driver support.)
- [ ] Are $T_1 \dots T_4$ taken from `CLOCK_MONOTONIC` (or a calibrated invariant TSC) — never `time.time()` / `CLOCK_REALTIME`?
- [ ] If `rdtsc` is used, is the TSC invariant (`CPUID.80000007H:EDX[8]`) and the cycles→ns conversion calibrated?

## Clock domains

- [ ] Is the NIC PHC → host-monotonic offset explicitly measured, documented, and refreshed — not assumed to be zero?
- [ ] Are all six timestamps converted to a single nanosecond base **before** a `HotPathTrace` is constructed?
- [ ] Where traces from multiple hosts are compared, are the PHCs disciplined to a common PTP grandmaster?

## Hot path

- [ ] Is timestamp recording in the trading loop non-blocking, lock-free and zero-allocation?
- [ ] Is all accounting, logging and percentile computation off the hot path?

## Budgets

- [ ] Does `phase_slas_ns` cover **every** phase in `PHASE_NAMES` (no typo'd or omitted keys)?
- [ ] Is it understood that phase budgets summing above `total_sla_ns` allow a total breach with no phase over its own budget — and is that intended?
- [ ] Are all values in **nanoseconds** (not microseconds)?
- [ ] Is the strict `>` breach semantics (equal-to-budget is not a breach) acceptable for the SLA being enforced?

## Ingestion and reporting

- [ ] Is `HotPathTrace` construction wrapped per trace, with rejects counted rather than dropped or clamped?
- [ ] Is the quarantine (reject) rate alerted on, and reported alongside the latency figures?
- [ ] Does the telemetry output $P_{50}, P_{95}, P_{99}, P_{99.9}$ **and** `count` for the total and every phase?
- [ ] Is every published tail figure backed by enough samples (~100 for $P_{99}$, ~1,000 for $P_{99.9}$)?
- [ ] Are SLAs evaluated on tails rather than the mean?

## Scope

- [ ] Is it understood that these monotonic deltas are **not** a UTC-traceable business-clock record for MiFID II RTS 25 purposes?
