---
name: cross-vendor-timestamp-precision-reconciliation
description: Market data reconciliation engine normalizing multi-vendor timestamps
  (s, ms, us, ns, ISO-8601) to 64-bit nanosecond UTC epoch with exact integer arithmetic,
  flagging out-of-order arrivals, precision shortfalls, and matched-event vendor skew.
domain: Data Management Global
subdomain: Market Data Reconciliation
tags:
- timestamp-reconciliation
- nanoseconds
- utc-epoch
- iso-8601
- databento
- refinitiv
- bloomberg
- mifid-ii-rts25
brokers_frameworks:
- Databento
- Refinitiv ELEKTRON
- Bloomberg B-PIPE
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a pipeline ingests market data from more than one vendor and the timestamps arrive in different units and encodings (float seconds, integer milliseconds, ISO-8601 strings, int64 nanoseconds — e.g. Databento publishes int64 UTC nanoseconds since epoch). Mixing them without exact normalization produces mis-ordered tick sequences, phantom latency measurements, and precision claims a firm cannot support in a MiFID II RTS 25 clock-traceability review. This module converts every raw timestamp into a 64-bit integer nanosecond UTC epoch using **exact integer/Decimal arithmetic**, flags out-of-order arrivals, audits the precision tier actually delivered, and measures cross-vendor skew between records that describe the *same* event.

## When NOT to Use

- **As a clock-synchronization monitor.** This engine compares timestamps *in data*. It cannot measure a host's offset from UTC — that is PTP/NTP telemetry; see `clock-drift-monitoring-alerting-thresholds` and `cross-datacenter-clock-sync-validation`. RTS 25 compliance is demonstrated with traceability evidence for the clock, not with a feed-comparison report.
- **To infer vendor clock error from a skew number.** A matched-event skew mixes the vendors' clock offsets with *where each vendor timestamps* — a matching-engine event time and a capture-NIC receive time legitimately differ by the network path (Databento, for instance, exposes both an event time and a receive time per record). Attribute a skew only after confirming both vendors' timestamping points.
- **When no shared event key exists.** Without an exchange sequence number or venue trade id, records from two vendors cannot be proven to describe the same event, and the interval between consecutive ticks is not a skew measurement. The engine skips the analysis rather than emitting unfounded warnings.
- **For sub-nanosecond or post-2262 timestamps.** int64 nanoseconds saturate at `2262-04-11T23:47:16.854775807Z`; both out-of-range values and sub-nanosecond fractional digits raise rather than being silently rounded.

## Prerequisites

- Vendor tick records with `tick_id` (unique), `vendor_id`, `symbol`, `raw_timestamp`, and `precision_format` (`SECONDS`, `MILLISECONDS`, `MICROSECONDS`, `NANOSECONDS`, `ISO8601`).
- Optional `event_key` (exchange sequence number / venue trade id) — required for any cross-vendor skew analysis.
- Optional `required_precision_tier` reflecting the applicable obligation (see `references/standards.md` for the RTS 25 and CAT figures) and a skew threshold `max_allowed_vendor_drift_ms`.

## Workflow

1. **Precision Normalization Engine** — `normalize_timestamp_to_ns()` returns `(ns_int64, iso_utc_str, precision_tier)`:
   - Numeric inputs are scaled with `Decimal`, never float: `int(1_700_000_000_123 * 1e6)` evaluates to `1700000000123000064`, because float64 has 53 significand bits and the representable spacing at 1.7e18 ns is 256 ns.
   - A **float** input is read as `Decimal(str(value))` — the shortest decimal that round-trips it — so `1700000000.123` becomes `1700000000123000000` ns rather than the binary tail `…122999808`. Decision point: a float declared as `NANOSECONDS` is **rejected**, because the float has already destroyed the precision the format claims.
   - **ISO-8601** is parsed with an explicit fractional-digit split, not `datetime`: `datetime` stops at microseconds and would silently drop the last three digits of `…20.123456789Z`. Offsets (`Z`, `±HH:MM`) are applied; a naive string is treated as UTC **with a warning**; more than 9 fractional digits raises unless the surplus is zero padding.
   - The precision tier for ISO input is derived from the **number of fractional digits** (0 → `SECONDS`, ≤3 → `MILLISECONDS`, ≤6 → `MICROSECONDS`, ≤9 → `NANOSECONDS`), so a millisecond feed is not recorded as microsecond-grade.
   - `iso_utc_str` is rendered by integer division with all 9 fractional digits — an audit string truncated to milliseconds does not document the value it accompanies.
   - Any result outside the signed 64-bit range raises.
2. **Temporal Alignment & Out-of-Order Detection**:
   - Ticks are walked in **arrival order**, and any tick whose timestamp precedes the highest timestamp already seen ($\Delta t < 0$) is flagged and counted. Decision point: inspecting adjacent pairs of the *sorted* list instead under-counts — arrivals $[5, 1, 2, 3]$ contain three late ticks but only one adjacent inversion survives sorting.
   - The batch is treated as **one merged stream**: the running maximum spans all symbols and vendors, which is what a sequencer or replay buffer needs. Decision point: if a late tick in one symbol should not count as out-of-order relative to another symbol, group the batch by symbol and reconcile each group separately.
   - Output is sorted by `(ns, arrival_index)` so equal timestamps keep a reproducible order.
   - Duplicate `tick_id` values raise: the id keys arrival ordering, and duplicates silently corrupt it.
3. **Precision Audit**: each tick is compared against `required_precision_tier`; shortfalls are flagged per record (`meets_precision_requirement`) and counted (`precision_violation_count`) rather than absorbed into a nanosecond schema.
4. **Matched-Event Skew Audit**: signed skew is computed only between different vendors sharing `(symbol, event_key)`; the sign identifies which vendor is ahead. Where one vendor reports the same event twice, its earliest timestamp is used. With no event keys present, the analysis is skipped and `skew_pairs_evaluated` is 0.
5. **Audit Report Generation**: a `TimestampReconciliationReport` carrying normalized ticks, out-of-order count, tier distribution, precision violations, and skew observations.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Scaling Through float64**: `int(ms * 1e6)` and `int(seconds * 1e9)` are the two most common conversions in market data code and both are wrong at nanosecond resolution — `1_700_000_000_123 ms` becomes `…123000064` and `1700000000.123 s` becomes `…122999808`. `int()` also truncates toward zero, so the error is a systematic bias, not noise. Use integer or `Decimal` arithmetic end to end.
- **Parsing Nanosecond ISO-8601 with `datetime`**: `datetime` resolution stops at microseconds, so `2023-11-14T22:13:20.123456789Z` silently loses its last three digits — a 789 ns error that no exception announces.
- **Ignoring Timezone Offsets**: a naive ISO string treated as UTC when the vendor emits local exchange time shifts every tick by hours. Require an explicit offset, or log loudly when assuming.
- **Zero-Padding Fake Precision**: appending six zeros to millisecond data to satisfy a nanosecond schema fabricates precision. Record the tier actually delivered and compare it against the obligation.
- **Mislabelling the Tier from the Schema**: precision is a property of the *value* (fractional digits present), not of the column type. Counting every ISO timestamp as microsecond-grade overstates the feed.
- **Calling an Inter-Tick Interval "Clock Drift"**: two vendors' consecutive ticks are usually two different events; their 20 ms gap says nothing about either clock. Skew is only meaningful between records matched on an exchange sequence number or trade id — and even then it includes the difference between each vendor's timestamping point.
- **Unsigned Skew**: reporting `abs(skew)` hides which vendor is ahead, which is the part that identifies the faulty feed.
- **Out-of-Order Counted After Sorting**: sorting destroys arrival information; detection must run on the arrival sequence.
- **int64 Overflow Assumed Away**: nanosecond epochs saturate int64 in 2262, and far-future or corrupt values silently wrap in downstream int64 columns unless the range is checked at ingest.

## Verification

- Normalize `1_700_000_000_123` as `MILLISECONDS` and assert exactly `1700000000123000000` (the float path yields `…123000064`).
- Normalize `1700000000.123` as `SECONDS` and assert exactly `1700000000123000000` (the float path yields `…122999808`).
- Normalize `"2023-11-14T22:13:20.123456789Z"` and assert `1700000000123456789` with tier `NANOSECONDS` and an ISO output carrying all 9 digits; normalize `"…20.123Z"` and assert tier `MILLISECONDS`.
- Pass `1.70000000012345678e18` as `NANOSECONDS` and expect `ValueError`; pass a year-3000 second value and expect the int64-range `ValueError`.
- Reconcile arrivals with timestamps `[5, 1, 2, 3]` and assert `out_of_order_count == 3`.
- Reconcile two different-vendor ticks 20 ms apart with **no** `event_key` and assert no drift warnings; add a shared `event_key` and assert a signed skew of `+20_000_000` ns with a warning, and that a skew of exactly the threshold does not warn.
- Configure `required_precision_tier="MICROSECONDS"`, submit one millisecond ISO tick and one nanosecond tick, and assert `precision_violation_count == 1`.
- Run `python -m unittest discover -s skills/cross-vendor-timestamp-precision-reconciliation/scripts`.

## Related Skills

- `clock-drift-monitoring-alerting-thresholds`
- `data-pipeline-schema-contract-testing`
- `clock-skew-correction-for-tick-timestamps`
- `cross-datacenter-clock-sync-validation`
