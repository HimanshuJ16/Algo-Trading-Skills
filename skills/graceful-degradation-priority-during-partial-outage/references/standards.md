# Standards — graceful-degradation-priority-during-partial-outage

## What is a requirement and what is a design choice

**The P1–P4 priority hierarchy is this library's engineering design, not a regulatory
requirement.** MiFID II RTS 6 contains no clause prioritising which functions continue
during a disruption. What regulation does require is that the firm keep the ability to
cancel, shut down cleanly, and still manage whatever is left outstanding — which is why
the engine's escalation flag, not its shedding order, is the compliance-relevant output.

## Verified regulatory touchpoints

Source: Commission Delegated Regulation (EU) 2017/589 (RTS 6), as reproduced in the
[FCA Handbook technical standards](https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1566)
and [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32017R0589).
Jurisdiction: EU/UK investment firms engaged in algorithmic trading. Mandatory.

| Provision | What it requires | How this skill touches it |
|---|---|---|
| Art. 12(1) | Ability to cancel immediately, as an emergency measure, any or all unexecuted orders submitted to any or all trading venues | The cancel path is P1 and is processed in every mode; a policy that sheds P1 is rejected at construction |
| Art. 14(1) | Business continuity arrangements appropriate to the nature, scale and complexity of the business, documented in a durable medium | `audit_notes` + `classification_reasons` are the per-decision record; the policy matrix is the documented arrangement |
| Art. 14(2)(b) | Adverse scenarios including unavailability of systems, staff, work space, external suppliers or data centres, or loss/alteration of data | Partial degradation and unreadable telemetry are two such scenarios, handled explicitly rather than by timeout |
| Art. 14(2)(e) | A usage policy for the Art. 12 kill functionality | Out of scope here — see `execution-algorithm-kill-switch-integration` |
| Art. 14(2)(f) | Arrangements for shutting down the relevant algorithm or trading system where appropriate | This engine degrades rather than shuts down; the shutdown decision sits above it |
| Art. 14(2)(g) | **Alternative arrangements to manage outstanding orders and positions** | `manual_intervention_required` fires whenever P1/P2 work was deferred or dropped, or telemetry was unreadable — the trigger for those alternative arrangements |
| Art. 14(3) | Shutdown must not create disorderly trading conditions | Why P2 exits are deferred and escalated rather than dropped, and why recovery is stepped rather than instantaneous |
| Art. 14(4) | Annual review and testing of the arrangements | The degradation paths must be exercised on a schedule — see the checklist |

**Not verified as existing:** no reviewed regulation specifies a CPU, packet-loss,
database-latency or telemetry-age threshold, a shedding order, or a recovery time for
algorithmic trading systems. Any such figure below is an engineering default.

## Engineering sources for the shedding design

Source: Beyer, Jones, Petoff & Murphy (eds.), *Site Reliability Engineering*, O'Reilly 2016.

| Claim | Location | Use here |
|---|---|---|
| Requests carry criticality levels (`CRITICAL_PLUS`, `CRITICAL`, `SHEDDABLE_PLUS`, `SHEDDABLE`) and a task "will only reject requests of a given criticality if it's already rejecting all requests of all lower criticalities" | [Ch. 21, *Handling Overload*](https://sre.google/sre-book/handling-overload/) | The monotonicity invariant enforced by `_validate_policy` |
| Criticality is propagated automatically through the RPC stack so downstream services respect the same ordering | Ch. 21 | Priority must be carried end to end; a router that re-derives it at each hop will disagree with itself |
| Graceful degradation reduces the work performed rather than failing outright; its code paths "rarely execute" and should be exercised deliberately | [Ch. 22, *Addressing Cascading Failures*](https://sre.google/sre-book/addressing-cascading-failures/) | Why the checklist requires a scheduled degradation drill |
| A service that entered cascading failure at 11,000 QPS does not recover by dropping to 9,000 QPS — load must fall well below the trigger and ramp back gradually | Ch. 22 | The stepped, confirmation-gated recovery (`recovery_confirmation_samples`), rather than instant restoration |
| "Always use randomized exponential backoff when scheduling retries"; consider a server-wide retry budget | Ch. 22 | Why a deferred backlog must be replayed staggered, not flushed |

## Library defaults (calibrate before use)

These are the engine's defaults, **not** industry standards, and none is derived from an
external source. Set them from your own measured percentiles and record the rationale.

| Parameter | Default | Effect |
|---|---|---|
| `partial_degradation_cpu_pct` | $75.0$ | At or above: defer P3, drop P4 |
| `critical_outage_cpu_pct` | $90.0$ | At or above: capital preservation |
| `partial_degradation_packet_loss_pct` | $1.0$ | At or above: defer P3, drop P4 |
| `critical_outage_packet_loss_pct` | $10.0$ | At or above: capital preservation |
| `partial_degradation_db_latency_ms` | `None` (disabled) | Ships disabled rather than with an invented figure; enable with your storage layer's calibrated percentile |
| `critical_outage_db_latency_ms` | `None` (disabled) | Tripping capital preservation on a latency number needs deliberate calibration |
| `max_health_sample_age_seconds` | `None` (disabled) | Enable and set to a small multiple of your sampling interval; once set, an unknown age fails safe |
| `recovery_confirmation_samples` | $3$ | Consecutive healthier samples required per one-level step down |

Comparisons are inclusive (`>=`): a sample exactly on a threshold degrades.

## Invariants enforced in code, not just documented

| Invariant | Enforcement |
|---|---|
| P1 is processed in every mode | `LoadSheddingConfigurationError` at construction if any policy row sheds P1 |
| Shedding is monotone in priority | `LoadSheddingConfigurationError` if a tier is shed harder than a lower tier |
| An unclassifiable task is never routed | `UnknownTaskPriorityError`; the whole batch is rejected |
| A non-finite metric never reads as healthy | `InvalidHealthMetricError`; `None` is the only way to say "unreadable" |
| A metric the configuration relies on but cannot read escalates | Classified `CRITICAL_OUTAGE`, `manual_intervention_required` set |
| Critical thresholds are never below partial thresholds | `LoadSheddingConfigurationError` at construction |
