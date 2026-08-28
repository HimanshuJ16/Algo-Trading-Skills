# Standards and Regulatory Touchpoints — Strategy-Specific Data Dependency Mapping

## What is mandated and what is not

**Nothing in this skill's scoring is a standard.** The criticality weights (CRITICAL 4,
HIGH 3, MEDIUM 2, LOW 1), the fallback credit (0.8), the degraded credit (0.5), the readiness
minimum (70%), and the freshness tiers below are operator-chosen defaults. No regulator,
exchange, or vendor publishes them. Calibrate them against your own incident history, record
who approved them, and version them with the rest of your risk configuration.

What *is* mandated, where the relevant regime applies, is that a firm maintain documented
arrangements for data and third-party dependency failure, monitor its trading in real time,
and keep the resulting controls under its own control. Those obligations are cited below.

## Freshness tier guidance (operator-chosen, not a standard)

A starting point for a per-feed `max_acceptable_lag_seconds`, to be replaced by a bound
derived from how fast the underlying quantity moves relative to the strategy's holding period.
The engine deliberately provides no default value for this field.

| Criticality | Typical feed | Illustrative bound | Default response when no vendor serves |
|---|---|---|---|
| CRITICAL | L2 order book, executable reference price | seconds | `BLOCK` — the strategy must not trade |
| HIGH | signal inputs, sentiment, borrow availability | tens of seconds to minutes | `DEGRADE` — reduced readiness credit |
| MEDIUM | fundamentals, corporate actions, static reference data | minutes | `DEGRADE` |
| LOW | enrichment, tagging, display metadata | minutes to hours | `DEGRADE` |

The default response column reflects the engine's `effective_failure_response`: `BLOCK` for
CRITICAL, `DEGRADE` otherwise, overridable per node. Choosing `DEGRADE` is a statement that
the strategy may trade on cached or imputed values for that feed.

## Regulatory touchpoints

Applicability depends on your jurisdiction, licence, and activity. Confirm each with your
compliance function; none of the below is legal advice, and the sources are cited so the
scope can be checked rather than assumed.

### EU / EEA — MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589

Applies to investment firms engaged in algorithmic trading.

- **Article 14 (Business continuity arrangements)** requires documented arrangements adapted
  to each trading venue accessed, covering a range of adverse scenarios expressly including
  the unavailability of external suppliers or data centres and the loss or alteration of
  critical data, and requires that a trading algorithm or system can be shut down without
  creating disorderly trading conditions. A per-strategy data dependency map with named
  vendors, failure responses, and a blast-radius projection is direct evidence for this
  article; the map alone is not the arrangement.
- **Article 16 (Real-time monitoring)** requires real-time monitoring of algorithmic trading
  activity with alerts, and specifies that **real-time alerts be generated within five seconds
  after the relevant event**. If a blocked dependency is wired into your alerting, that budget
  constrains how often readiness is evaluated and how quickly a block is escalated.
- **Article 12 (Kill functionality)** and **Article 15 (Pre-trade controls on order entry)**
  cover the controls this skill does *not* provide. A data-readiness gate is upstream of, and
  no substitute for, either.

Source: <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj>

### EU / EEA — DORA, Regulation (EU) 2022/2554

Applies to EU financial entities in scope; applicable since **17 January 2025**.

- **Article 28** requires a register of information covering all contractual arrangements for
  ICT services provided by third-party providers, maintained at entity, sub-consolidated and
  consolidated level, distinguishing arrangements that support **critical or important
  functions** from those that do not, and submitted to the competent authority in the
  harmonised ITS format.
- The criticality tier and vendor list on each `DataDependencyNode` map onto that distinction
  and can feed the register, but the register is a contractual inventory, not a runtime one.
- Whether a given market-data arrangement qualifies as an "ICT service" under Article 3(21)
  has needed supervisory clarification; confirm the classification per contract rather than
  assuming every data feed is in or out of scope.

Source: <https://eur-lex.europa.eu/eli/reg/2022/2554/oj>

### EU / EEA — MiFID II RTS 25, Commission Delegated Regulation (EU) 2017/574

Applies to trading venue operators and their members/participants, for the timestamps of
reportable events.

- Annex Table 2 sets maximum divergence from UTC for members and participants: **100 µs** with
  1 µs granularity for high-frequency algorithmic trading, **1 ms** with 1 ms granularity for
  any other trading activity, and 1 second for voice, RFQ with human intervention, and
  negotiated transactions.
- These bounds govern reportable-event timestamps, **not** vendor feed publication timestamps.
  The relevance here is indirect but real: a staleness calculation subtracts a vendor's clock
  from yours, so an undisciplined clock on either side makes the result meaningless. This is
  why the engine treats a timestamp further ahead of the evaluation clock than
  `future_timestamp_tolerance_seconds` as a `CLOCK_SKEW` fault rather than as fresh data.

Source: <https://eur-lex.europa.eu/eli/reg_del/2017/574/oj>

### US — SEC Rule 15c3-5 (Market Access Rule), 17 CFR 240.15c3-5

Applies to broker-dealers with market access, or providing others with access to an exchange
or ATS. Not directly a data-quality rule.

- Rule 15c3-5(b) requires the risk management controls and supervisory procedures to be under
  the **direct and exclusive control** of the broker-dealer with market access, subject to
  limited exceptions permitting reasonable allocation to another registered broker-dealer.
- The implication for this skill is a boundary, not a requirement: a data-readiness gate is a
  governance control over your own inputs. It does not discharge the pre-trade financial and
  regulatory controls the rule requires, and those controls cannot be outsourced to a data
  vendor.

Source: <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>

## Interoperability

**OpenLineage** (<https://openlineage.io>) is an LF AI & Data graduated open standard defining
a common event format for lineage metadata across pipeline tools. The reference engine in
`scripts/` does **not** emit or consume OpenLineage events — its graph is an in-process model
of one strategy's feed dependencies, not a lineage collection protocol. If you need
interoperable lineage across a wider data platform, OpenLineage is the relevant specification
and this engine's node/edge inventory is a plausible source for it; treat that as integration
work, not as a feature of this skill.
