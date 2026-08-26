# Standards for Load Testing Before a Universe Scale-Up

## What is actually mandated, and on whom

| Requirement | Who it binds | Status | Source |
|---|---|---|---|
| Stress test that algorithmic trading systems and controls "can withstand increased order flows or market stresses", as part of the annual self-assessment, including high messaging volume and high trade volume tests | EU/EEA investment firms engaged in algorithmic trading | **Mandatory** | RTS 6, Art. 10 (Commission Delegated Regulation (EU) 2017/589) |
| The message-volume test level: systems "would need to withstand twice the volume of messages or trades processed in the previous 6 months" | Same firms; ESMA's reading of RTS 6 Art. 10 | Supervisory guidance — the briefing states its content "is non-binding and not subject to a 'comply or explain' mechanism" | ESMA Supervisory Briefing on Algorithmic Trading in the EU, §32 (26 Feb 2026) |
| Retesting when an algorithm's **scope** changes — ESMA lists "Deploying the algorithm in new instruments, venues, or asset classes" as a material change | Same firms | Supervisory guidance | ESMA Supervisory Briefing, §31 table (26 Feb 2026) |
| "The establishment of reasonable current and future technological infrastructure capacity planning estimates" and "periodic capacity stress tests of such systems" | **SCI entities only** — SCI SROs, SCI ATSs, plan processors, exempt clearing agencies subject to ARP, SCI competing consolidators | Mandatory for those entities | 17 CFR 242.1001(a)(2) (Regulation SCI) |

Two applicability boundaries matter and are easy to get wrong:

- **Regulation SCI does not reach an ordinary broker-dealer or proprietary trading firm.** The
  capacity-planning and capacity-stress-test obligations above attach to the *SCI entity*
  definition. A US algorithmic trading firm scaling its instrument universe has no direct
  federal analogue of RTS 6 Art. 10; SEC Rule 15c3-5 governs pre-trade risk controls, not
  infrastructure capacity. Do not cite Reg SCI at a firm that is not an SCI entity.
- **RTS 6 Art. 10 is a floor expressed against observed volume, not against an average.**
  It anchors on twice the *highest* messages received and sent in the previous six months.
  This skill's `peak_volatility_multiplier` instead scales an *average* per-symbol rate. If
  your observed peak-to-average ratio is 8x, then a 5x multiplier on the average is well
  below a 2x-of-observed-peak floor. Convert before claiming compliance.

## Utilization headroom — a heuristic, not a standard

`max_safe_utilization_pct = 80.0` is this skill's default operating ceiling. It is an
engineering convention; no regulator or exchange publishes it. The reason to leave headroom
is queueing, not a rule: in an M/M/1 queue mean response time scales with `1/(1 - rho)`, so
latency degrades sharply well before a resource is "full", and a link averaging 80% over a
one-second window can be fully saturated for a 10 ms microburst. `capacity-planning-for-symbol-universe-growth`
defaults to a stricter 60% network ceiling for the same reason. Pick the number deliberately
and record why; do not present it as a mandate.

## The other multipliers this skill applies

| Multiplier | Default | Provenance |
|---|---|---|
| `peak_volatility_multiplier` | 5.0 | **Unsourced placeholder.** Derive it from your own observed peak-to-average ratio, measured on a sub-second window. |
| `memory_allocation_buffer` | 1.25 (+25%) | **Unsourced heuristic** for allocator overhead and fragmentation. Not a published standard. |
| `wire_overhead_factor` | 1.0 (payload only) | Charges payload bytes only, so it **under-states** wire bandwidth — the dangerous direction. See below. |
| `ticks_per_write_io` | 1.0 (one IO per persisted tick) | **Over-states** IOPS for any batching writer, so it errs conservatively. Set your measured batch factor. |

None of the per-symbol defaults (`avg_ticks_sec_per_symbol`, `bytes_per_tick`,
`memory_mb_per_orderbook`) are measured constants either. Each scales the whole projection
linearly, so an unmeasured default produces a confidently wrong verdict.

## Network bandwidth: payload is not wire cost

This skill multiplies payload bytes by the message rate. Real wire cost adds per-*packet*
framing (Ethernet + IPv4 + UDP/TCP headers, per IEEE 802.3 / RFC 791 / RFC 768 / RFC 793),
and is affected by transport batching, A/B multicast redundancy, and retransmission traffic.
Those are modelled properly in `capacity-planning-for-symbol-universe-growth`; its
`references/standards.md` carries the byte constants and the feed-operator multipliers with
citations. Size bandwidth there, then pass the resulting ratio into `wire_overhead_factor`
here rather than duplicating the model.

## Database write IOPS is not the persisted message rate

`projected_db_iops` is persisted ticks per second divided by `ticks_per_write_io`. With the
default of 1.0 it assumes one storage write operation per persisted tick. Real writers
coalesce: group-commit WAL, LSM-tree memtable flush, columnar batch insert. The default
therefore over-provisions rather than under-provisions, but it will fail a scale-up that
your storage layer could actually absorb. Measure the batch factor before treating an
`LOAD_TEST_FAILED_IOPS_EXCEEDED` verdict as final.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 9-10 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
- ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 February 2026 (§31 change-type table, §32 stress testing) — https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf
- 17 CFR 242.1001, *Obligations related to policies and procedures of SCI entities* — https://www.law.cornell.edu/cfr/text/17/242.1001
- Wire overhead constants, feed redundancy and burst-window guidance: `skills/capacity-planning-for-symbol-universe-growth/references/standards.md`
