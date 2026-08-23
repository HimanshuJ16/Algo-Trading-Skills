# Standards for Cross-Vendor Timestamp Precision Reconciliation

## Engineering Standards

| Metric | Engineering Standard |
|---|---|
| Normalized Format | All multi-vendor market data timestamps MUST be stored as 64-bit integer nanoseconds UTC ($t_{\text{ns}}$), and the value MUST be range-checked against the signed int64 bounds (nanosecond epochs saturate at `2262-04-11T23:47:16.854775807Z`). |
| Float Precision Prohibition | Timestamps MUST NOT be scaled or stored through IEEE 754 binary64. float64 carries a 53-bit significand, so the representable spacing at an epoch value of ~1.7e18 ns is 256 ns: `int(1_700_000_000_123 * 1e6)` evaluates to `1700000000123000064`, and `int(1700000000.123 * 1e9)` to `1700000000122999808`. Use integer or `Decimal` arithmetic. |
| Float Source Reconstruction | Where a vendor genuinely delivers float seconds, the value MUST be reconstructed via the shortest round-tripping decimal (`Decimal(str(x))`), not the raw binary expansion — and the record MUST NOT then be labelled better than microsecond grade (float64 resolution at epoch magnitude is ~238 ns). |
| Nanosecond ISO-8601 Parsing | ISO-8601 fractional seconds MUST be parsed as integers, not via `datetime` (whose resolution stops at microseconds and silently discards digits 7-9). Sub-nanosecond fractional digits MUST raise rather than round. |
| Precision Tier Attribution | The recorded precision tier MUST be derived from the value delivered (fractional digits present), never from the schema's column type. Zero-padding coarse data into a nanosecond schema without flagging the tier is prohibited. |
| Timezone Explicitness | ISO-8601 input without an explicit offset MUST be logged when interpreted as UTC; silent local-time assumptions shift every tick by the venue's offset. |
| Out-Of-Order Tolerance | Out-of-order detection MUST run over the ARRIVAL sequence, flagging any tick with $\Delta t < 0$ against the running maximum. Adjacent-pair inspection of the sorted output under-counts (arrivals `[5,1,2,3]` produce 3 late ticks but only 1 adjacent inversion). |
| Record Key Integrity | `tick_id` MUST be unique within a reconciliation batch; duplicates corrupt arrival ordering silently. |
| Skew Requires Matched Events | Cross-vendor timestamp skew MUST be computed only between records from different vendors sharing an event key (exchange sequence number / venue trade id), and MUST be reported signed. The interval between two consecutive ticks is not clock drift. |
| Deterministic Output | Ordering MUST be stable for equal timestamps (tie-break on arrival index) so identical input yields an identical audit artifact. |

## Regulatory Anchors (verify currency before relying on them)

This engine reconciles timestamps *in data*. Neither regime below is satisfied by a
feed-comparison report: both are obligations on the reporting entity's own clocks and
its documented traceability to a reference time source.

| Regime | Provision | Requirement |
|---|---|---|
| EU — MiFID II RTS 25 | Commission Delegated Regulation (EU) 2017/574, Art. 2 + Annex Table 1 (trading venue operators) | Gateway-to-gateway latency above 1 ms: maximum divergence from UTC **1 millisecond**, granularity **1 ms or better**. Latency of 1 ms or less: maximum divergence **100 microseconds**, granularity **1 microsecond or better**. |
| EU — MiFID II RTS 25 | Reg. (EU) 2017/574, Art. 3 + Annex Table 2 (members/participants) | High-frequency algorithmic trading technique: divergence **100 us**, granularity **1 us or better**. Voice trading, RFQ with human intervention, negotiated transactions: divergence **1 second**, granularity **1 s or better**. Any other trading activity: divergence **1 millisecond**, granularity **1 ms or better**. RTS 25 nowhere requires nanosecond granularity. |
| EU — MiFID II RTS 25 | Reg. (EU) 2017/574, Art. 4 | Traceability to UTC must be documented: system design, the points at which timestamps are taken, and an annual review of compliance. |
| US — CAT NMS Plan | Consolidated Audit Trail clock synchronization standard | Industry Members: Business Clocks within **50 milliseconds** of NIST; Participants (exchanges): within **100 microseconds** of NIST; clocks used solely for Manual Order Events (and, for Industry Members, allocation timestamps): within **1 second**. |
| US — FINRA | FINRA Rule 4590 | Clocks recording events in NMS securities, standardized options and OTC equity securities synchronized within a **50 millisecond** tolerance of the NIST clock (FINRA has proposed clarifying that Rule 4590 applies where Rule 6820, the CAT clock-synchronization rule, does not). |

## Vendor Notes

| Vendor | Observed convention | Note |
|---|---|---|
| Databento | `ts_event` / `ts_recv` as int64 UTC nanoseconds since epoch | Multiple timestamps per record capture different points (matching-engine event time vs capture receive time), so comparing one vendor's event time against another's receive time measures the network path, not a clock offset. |
| Bloomberg B-PIPE, Refinitiv ELEKTRON | Treated here as illustrative examples of float-second and ISO-8601 encodings | The engine is format-driven, not vendor-driven: the caller declares `precision_format`. Confirm the actual field semantics against the vendor's own schema documentation before configuring a feed. |

Sources consulted (Aug 2026): EUR-Lex CELEX:32017R0574 (RTS 25) Annex Tables 1-2 and Arts. 2-4;
CAT NMS Plan clock-synchronization FAQ (catnmsplan.com); FINRA Rule 4590 and FINRA rule-filing
material on its interaction with Rule 6820; Databento documentation on timestamp fields and
nanosecond conventions. The float64 behaviour stated above was reproduced directly in CPython 3.11.
