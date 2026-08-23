# Cross-Datacenter Clock Agreement: Sources and Scope

Two kinds of number appear below and they must not be mixed. **Regulatory ceilings** are
published by a regulator and bind a named activity in a named jurisdiction. **Engineering
tiers** are what this module ships as defaults; no regulator publishes a pairwise
inter-datacenter drift limit, so those defaults are operational conventions, not law.

## 1. What this module measures, and why it is not an RTS 25 check

RTS 25 bounds **each business clock against UTC**. This module measures **two sites against
each other**. Neither implies the other:

- Two clocks each within 100 µs of UTC may be up to 200 µs apart from one another. Pairwise
  drift is bounded by the *sum* of the two UTC divergences, so a pair of RTS 25
  HFT-compliant clocks implies a pairwise budget of 2 × 100 µs. That derivation is
  arithmetic, not a published figure — it is exposed as
  `MIFID_HFT_IMPLIED_PAIRWISE_BUDGET_MS`.
- Two clocks that agree perfectly with each other may both sit 10 ms from UTC. Pairwise
  agreement is evidence of nothing whatsoever about UTC divergence.

Use this module for cross-region event ordering. Use
`clock-drift-monitoring-alerting-thresholds` for the UTC divergence obligation.

## 2. EU — MiFID II RTS 25 (the UTC ceilings this budget is derived from)

**Commission Delegated Regulation (EU) 2017/574** of 7 June 2016, supplementing Directive
2014/65/EU with regard to regulatory technical standards for the level of accuracy of
business clocks.
<https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0574>

**Article 3 + Annex Table 2 — members or participants of a trading venue**, by type of
trading activity:

| Type of trading activity | Maximum divergence from UTC | Granularity |
|---|---|---|
| High frequency algorithmic trading technique | 100 microseconds | 1 microsecond or better |
| Any other trading activity | 1 millisecond | 1 millisecond or better |
| Voice / RFQ with human intervention / concluding negotiated transactions | 1 second | 1 second or better |

**Article 2 + Annex Table 1 — operators of trading venues**, scoped by the trading system's
gateway-to-gateway latency rather than by activity: latency > 1 ms → 1 ms divergence / 1 ms
granularity; latency ≤ 1 ms → 100 µs divergence / 1 µs granularity.

**Article 4 — traceability.** Entities must establish a system of traceability to UTC,
document its design, functioning and specifications, identify the exact point at which a
timestamp is applied, and review it at least annually. This validator produces evidence
*inside* such a system; it is not the system.

These figures are EU-scoped and do not generalize — a US CAT reporter is bound by FINRA
Rule 6820 to 50 **milliseconds** against NIST, some 500× looser. Configure the limit from
the row that binds you.

## 3. Measurement uncertainty — why RTT/2 is added, never subtracted

**chrony**, `chronyc` documentation, `tracking` command. The absolute bound on the local
clock's error is published as:

    clock_error <= |system_time_offset| + root_dispersion + (0.5 * root_delay)

<https://chrony-project.org/doc/4.5/chronyc.html>

"Root delay" is the total network path delay to the stratum-1 source, and "Root dispersion"
the dispersion accumulated back to it. Half the root delay is an irreducible uncertainty on
the offset, because the offset calculation assumes a symmetric path.

**RFC 5905** (NTPv4), §8 gives the on-wire offset and delay:

    theta = 1/2 * [(T2 - T1) + (T3 - T4)]
    delta = (T4 - T1) - (T3 - T2)

and §4 defines the synchronization distance `LAMBDA = EPSILON + DELTA / 2`, which
"represents the maximum error due to all causes".
<https://www.rfc-editor.org/rfc/rfc5905.html>

Both sources agree on the sign: half the path delay **widens** the error bound. A drift
formula of the form `|T_A − T_B| − RTT/2` — which earlier revisions of this skill carried —
inverts that and reports accuracy that was never measured. It is corrected in
`scripts/clock_sync_validator.py`.

## 4. Engineering tiers shipped as defaults

No regulator publishes a pairwise inter-datacenter drift limit. These are this module's
defaults and are all constructor arguments; set them from your own measured infrastructure
and the ceiling that binds your activity.

| Health tier | Pairwise drift | Arbitration |
|---|---|---|
| `EXCELLENT` | ≤ `excellent_drift_ms` (0.1 ms) | Permitted |
| `ACCEPTABLE` | ≤ `max_allowed_drift_ms` (1.0 ms) | Permitted |
| `DEGRADED` | ≤ `degraded_ceiling_ms` (5.0 ms) | **Denied** — severity label, not a permission |
| `BREACH` | > `degraded_ceiling_ms` | **Denied** |
| `UNKNOWN` | no verdict possible | **Denied** (fail closed) |

Arbitration additionally requires `is_measurement_conclusive`: when the combined
uncertainty of a pair exceeds `max_allowed_drift_ms`, the measurement cannot evidence the
limit and the verdict is denied regardless of the point estimate.

## 5. Resolution floor

`timestamp_sec` is a float64 holding a Unix-epoch value. At that magnitude (~1.8 × 10⁹) the
ULP is ≈ 0.238 µs, exposed as `RESOLUTION_FLOOR_MS`. Drift figures derived from that field
are not meaningful below roughly 1 µs. This is a property of the representation, not of the
clocks. Carry fine-grained offsets in `reported_offset_ms`, which stays small and precise.

## Category

`real-time-architecture` — see top-level `mappings/` directory.
