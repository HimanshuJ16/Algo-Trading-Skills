# Standards for Custody Solution Uptime and Liveness Guarantees

## 1. What is a standard here, and what is not

The single most common error in this area is quoting "99.9%" as though a standards
body mandated it. Nothing in this table does.

| Item | Source | What it actually says | Status |
| :--- | :--- | :--- | :--- |
| Uptime target (e.g. 99.9%) | Your executed provider agreement | Whatever you negotiated, over whatever measurement window the contract defines | **Contractual** — an input to this module, never a constant |
| Signing latency ceiling | Your executed provider agreement | Contractual, and frequently absent from standard terms | **Contractual** |
| SOC 2 Availability | AICPA Trust Services Criteria, TSP Section 100 (2017 criteria with revised points of focus, 2022) | A1.1 capacity management, A1.2 environmental protections / backup / recovery infrastructure, A1.3 recovery-plan testing — each qualified "**to meet its objectives**" | **Attestation, not a threshold** |
| k-of-n signing quorum | Threshold-signature scheme design (see NIST IR 8214 for the general treatment) | At least `k` shares must participate to produce a signature | **Definitional** |

### SOC 2 does not set an uptime number

The three Availability criteria read, verbatim:

> **A1.1** The entity maintains, monitors, and evaluates current processing capacity and use of system components (infrastructure, data, and software) to manage capacity demand and to enable the implementation of additional capacity to help meet its objectives.
>
> **A1.2** The entity authorizes, designs, develops, or acquires, implements, operates, approves, maintains, and monitors environmental protections, software, data backup processes, and recovery infrastructure to meet its objectives.
>
> **A1.3** The entity tests recovery plan procedures supporting system recovery to meet its objectives.

Every one ends in *"to meet its objectives"*. A SOC 2 Type II report attests that the
service organization met **its own stated service commitments** over the review
period; it does not define what those commitments must be. A custodian can hold a
clean SOC 2 Type II with the Availability category in scope and still owe you no
particular uptime percentage. Treat SOC 2 as evidence that controls exist and were
tested, and the contract as the source of the number.

### Published vendor figures

No numeric SLA is hard-coded in this skill, deliberately. Fireblocks' public
*Direct Custody Principles* page states the company "operates on a strict SLA with
24 / 7 / 365 support and engineering monitoring" and directs readers to
`status.fireblocks.com` for disruptions, but publishes **no uptime percentage** on
that page. Third-party summaries quoting specific figures could not be traced to a
primary vendor source at the time of writing. Take your number from the executed
agreement, corroborate against the provider's status-page history, and record both
in `custody-solution-vendor-due-diligence-checklist`.

## 2. Engineering standards this module enforces

| Control | Rule | Rationale |
| :--- | :--- | :--- |
| Fail-closed on absent telemetry | Zero probes MUST yield `UNKNOWN_NO_TELEMETRY` and recommend failover | A monitor that reports healthy while blind reports healthy through the incident it exists to catch |
| Fail-closed on stale telemetry | Newest probe older than `max_probe_age_ms` MUST yield `STALE_TELEMETRY` and recommend failover | A frozen collector is indistinguishable from a healthy custodian unless age is checked |
| Fail-closed on malformed telemetry | Non-finite, negative, or structurally impossible probes MUST raise | `NaN > threshold` is False, so a corrupt reading silently passes every gate |
| No rounding before comparison | Uptime MUST be compared on the value reported, not a coarser rounding of it | Rounding 99.896% to 99.90% clears a 99.9% target it actually missed |
| Chronological ordering | The "current" node count MUST be taken from the newest probe by `timestamp_ms`, not by list position | Concurrent collectors deliver out of order |
| Quorum early warning | `active == k` MUST be reported as `QUORUM_AT_RISK` | Zero remaining redundancy; the next node loss halts signing |
| Percentile sample gate | A P99 MUST NOT gate an SLA decision below `min_latency_samples` (default 100) | A 99th percentile cannot resolve the top 1% of fewer than 100 observations |
| Concurrent breach reporting | All breached conditions MUST be reported, not only the most severe | The quorum alarm otherwise masks the uptime breach that explains it |

## 3. Definitions

**Uptime (as computed here).** Probe-success ratio over the supplied window:

$$\text{Uptime \%} = \frac{N_{\text{healthy}}}{N_{\text{total}}} \times 100$$

This equals contractual time-weighted availability only when probes are evenly
spaced. `probe_count`, `window_start_ms`, and `window_end_ms` are reported so the
caller can check that assumption.

**MPC quorum.** For a k-of-n threshold scheme, at least `k` shares must participate
to produce a signature, so the cluster tolerates `n - k` unavailable nodes:

$$\text{redundant\_nodes} = \text{active} - k$$

`redundant_nodes > 0` healthy · `= 0` at risk · `< 0` signing halted.

**P99 signing latency.** Linear ("type-7") interpolation over healthy-probe
latencies: rank $= q(n-1)$, interpolating between neighbouring order statistics.
The estimator is pinned and stated because nearest-rank and type-6 definitions put
the same sample at a different p99, and an SLA gate must be reproducible.

## 4. Sources

- AICPA Trust Services Criteria, TSP Section 100 — Availability criteria A1.1–A1.3 (2017 criteria with revised points of focus, 2022).
- [Linford & Co., *Availability Trust Services Criteria/Principle in a SOC 2*](https://linfordco.com/blog/availability-soc-2-trust-service-principle/) — availability targets derive from the service organization's own contractual commitments and SLAs, not from SOC 2.
- [Fireblocks, *Direct Custody Principles*](https://www.fireblocks.com/principles) — states an SLA and 24/7/365 monitoring exist; publishes no uptime percentage.
- [NIST IR 8214, *Threshold Schemes for Cryptographic Primitives*](https://nvlpubs.nist.gov/nistpubs/ir/2019/NIST.IR.8214.pdf) — general treatment of threshold schemes and their availability/secrecy trade-offs.
- [Google SRE Book, *Service Level Objectives*](https://sre.google/sre-book/service-level-objectives/) — high-order percentiles as a plausible worst case; SLO targets are meaningful only with their measurement window.
