# Risk-Control Latency Standards

## What is actually mandated, and what is not

**No regulator publishes a numeric latency budget for a pre-trade risk check.** The 50 ms
default in `scripts/risk_latency_budgeter.py` is an engineering placeholder for testing, not
policy, and must be replaced with a value derived from your own measured capacity. Everything
below is either a mandatory obligation with no attached number, a mandatory obligation with a
number that governs something *adjacent* to the check, or a clock-accuracy tolerance that
bounds how small a budget you can credibly measure at all.

### EU / UK — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

Applies to investment firms engaged in algorithmic trading. Retained in the UK as assimilated
law in the FCA Handbook's technical standards.

| Article | Requirement | Numeric deadline |
|---|---|---|
| 12 — Kill functionality | A firm "shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected", and must be able to identify which algorithm, trader, desk or client is responsible for each order. | None stated — "immediately" is not quantified. |
| 15 — Pre-trade controls on order entry | Automatic blocking or cancellation of orders from a trader without permission to trade an instrument, and of orders risking the firm's own risk thresholds. | None. |
| 16(5) — Real-time monitoring | "Real-time alerts shall be generated within five seconds after the relevant event." | **5 seconds.** |
| 10 — Stress testing | Test using "the highest number of messages received and sent by the investment firm during the previous six months, multiplied by two". | 2× peak six-month message rate. |

Read Article 16(5) precisely: five seconds is the deadline for **alert generation**, not for the
risk check, the cancel, or the venue acknowledgement. Quoting it as a risk-control SLA
overstates the requirement in one direction and understates the engineering need in the other —
a kill switch that first acts 4.9 seconds after a breach is inside Article 16(5) and useless.

Article 12 is the reason this skill distinguishes dispatch from acknowledgement: an obligation to
*cancel* is discharged by the venue acting, not by the firm's socket write.

### EU / UK — MiFID II RTS 25 (Commission Delegated Regulation (EU) 2017/574), clock synchronisation

Annex Table 2 — members and participants of a trading venue:

| Type of trading activity | Maximum divergence from UTC | Timestamp granularity |
|---|---|---|
| High-frequency algorithmic trading technique | 100 µs | 1 µs or better |
| All other trading activity | 1 ms | 1 ms or better |
| Voice trading systems | 1 s | 1 s or better |
| RFQ where the response requires human intervention | 1 s | 1 s or better |
| Negotiated transactions | 1 s | 1 s or better |

Annex Table 1 applies to venue operators: 100 µs divergence / 1 µs granularity where
gateway-to-gateway latency is ≤ 1 ms, otherwise 1 ms / 1 ms. Article 4 requires traceability to
UTC, documentation of the design and of the exact points at which timestamps are applied, and a
compliance review at least annually.

**Implication for a latency budget:** these are the *permitted* divergences, so two compliant
clocks bracketing one measurement can differ by twice the tolerance. A budget of the same order
as that figure cannot be certified across two hosts — put both boundaries on one clock domain or
report the trace as `UNCERTAIN`.

### US — SEC Rule 15c3-5 (17 CFR 240.15c3-5), market access risk controls

- **(c)(1)** — controls must be reasonably designed to systematically limit financial exposure,
  including preventing entry of orders exceeding pre-set credit or capital thresholds "by
  rejecting orders", and preventing erroneous orders by rejecting those outside price or size
  parameters "on an order-by-order basis or over a short period of time".
- **(c)(2)** — regulatory controls, including preventing entry of orders unless pre-order-entry
  regulatory requirements are satisfied.
- **(d)** — the controls "shall be under the direct and exclusive control of the broker or
  dealer". Outsourcing the *speed* problem to a vendor does not outsource the obligation.
- **(e)** — at least annual review of effectiveness, documented, plus an annual CEO
  certification.

The rule text prescribes **no latency figure**. The obligation is that the check happens before
entry and works; how fast it must run is an engineering determination the firm has to make and
evidence. Rejecting orders pre-entry is inherently on the critical path — a design that bypasses
the check under load to preserve throughput does not satisfy (c)(1).

## Measurement standards

- **Use a monotonic clock for durations.** `CLOCK_REALTIME` is "affected by discontinuous jumps
  in the system time ... and by frequency adjustments performed by NTP and similar
  applications". `CLOCK_MONOTONIC` "is not affected by discontinuous jumps in the system time"
  but "is affected by frequency adjustments"; `CLOCK_MONOTONIC_RAW` "provides access to a raw
  hardware-based time that is not subject to frequency adjustments". Neither monotonic clock
  counts time the system is suspended. In Python: `time.perf_counter_ns()` /
  `time.monotonic_ns()`, never `time.time()`.
- **Reject, never clamp.** Non-monotonic, NaN, infinite, or cross-domain timestamp sequences are
  measurement failures. A negative interval clamped to zero becomes the best latency in the
  dataset.
- **Separate the semantics.** Event, decision, dispatch, acknowledgement, cancellation, and
  effective containment are distinct end states. Local dispatch is not proof of venue action.
- **Budget per control, scope, and session**, from measured capacity evidence, and re-validate
  under the stress volumes above. Illustrative values are not production policy.
- **Keep the measurement bounded and non-blocking.** Export asynchronously, cap retention, and
  expose drops. A passing trace must not cost an operator-visible log line.
- **Segment percentiles by control and scope, and declare the sample count.** Nearest-rank P99
  requires at least 100 samples; below that the figure is the maximum. Exclude untrusted samples
  from the distribution rather than averaging them in.
- **Alert on breach, uncertainty, missing acknowledgements, queue age, clock skew, retries,
  stale control configuration, and actuator failure** — and on the *absence* of traces.
- **On a safety-critical breach, invoke and verify the approved fail-safe action.** An alert
  alone is insufficient; confirm broker/exchange state afterwards.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Articles 10, 12, 15, 16 —
  <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589>; Article 16 as
  assimilated in the FCA Handbook —
  <https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1568>
- Commission Delegated Regulation (EU) 2017/574 (RTS 25), Article 4 and Annex Tables 1-2 —
  <https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0574>
- 17 CFR § 240.15c3-5, paragraphs (b)-(e) —
  <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>
- `clock_gettime(2)`, Linux man-pages — <https://man7.org/linux/man-pages/man2/clock_gettime.2.html>

Jurisdiction note: RTS 6 and RTS 25 bind EU investment firms (and, as assimilated law, UK
firms); Rule 15c3-5 binds US broker-dealers with market access. Neither set transfers to the
other, and neither substitutes for venue rulebooks or for local requirements in other
jurisdictions. Verify currency against the primary sources before relying on any figure here.
