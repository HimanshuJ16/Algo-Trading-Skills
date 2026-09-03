# Standards Reference — structured-logging-for-post-incident-forensics

> **Scope note.** This file documents (a) the record schema this skill emits and (b) the
> recordkeeping and time-accuracy regimes that commonly determine what a firm must do
> with those records. It is engineering guidance, not legal advice. Which regime binds
> you depends on your jurisdiction, your registration status, and the venue — a
> proprietary trader running a bot against a retail broker API is not a registered
> broker-dealer and is not bound by SEC Rule 17a-4. Read the regime, not this table.

## 1. Record schema (`SCHEMA_VERSION = "2.0.0"`)

One JSON object per line. Field order is stable so a diff of two records is readable.

| Field | Type | Required | Purpose and why it is shaped this way |
|---|---|---|---|
| `schema_version` | string | Yes | These records outlive the parser that reads them. A five- or six-year archive with no version stamp cannot be parsed safely after the schema moves. |
| `seq` | integer | Yes | Monotonic within one logger instance. Assigned under the same lock that inserts the event, so it is a total order over that instance's events. **The only ordering field** — not the timestamp, not file order. |
| `instance_id` | string | Yes | `"<pid>-<8 hex>"`. Scopes `seq`. Sequence numbers restart at 1 in every process, so `(instance_id, seq)` — never `seq` alone — is the sort key across restarts and merged logs. The PID cross-references OS and supervisor logs; the random suffix survives PID reuse. |
| `ts_ns` | integer | Yes | Nanoseconds since the Unix epoch, UTC. Integer, matching the OpenTelemetry Logs Data Model `Timestamp` (`uint64` nanoseconds since epoch). Integer rather than float because a float epoch-seconds field spends its precision on the seconds and loses sub-microsecond digits — the range that matters where a clock-granularity obligation exists. |
| `ts_iso` | string | Yes | RFC 3339 UTC, nine fractional digits, `Z` suffix. Rendered from `ts_ns`, so it is a presentation of the authoritative value rather than a second source of truth. |
| `mono_ns` | integer | Yes | `time.monotonic_ns()`, read under the same lock as `seq`, hence non-decreasing in `seq`. Elapsed times computed from this survive an NTP step; elapsed times computed from `ts_ns` do not. Comparable **only** within one `instance_id`. |
| `event_type` | string | Yes | A member of the `EventType` taxonomy, or a caller-supplied string flagged with `_unknown_event_type`. |
| `correlation_id` | string | Yes | 32 lowercase hex characters when generated here. Links every event in one order's lifecycle. |
| `component` | string | Yes | Source component (`"oms"`, `"feed-handler"`, `"risk"`). |
| `severity` | string | Yes | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. A closed set; an unresolvable value is filed at `ERROR` with `_invalid_severity` preserving the raw input. |
| `severity_number` | integer | Yes | OpenTelemetry `SeverityNumber`: 5 / 9 / 13 / 17 / 21 for the five levels above. Survives a move into an OTel-shaped pipeline without a lossy re-derivation from the text. |
| `message` | string | Yes | Human-readable. Newlines and control characters are JSON-escaped, so a message cannot forge an extra record in a line-delimited sink. |
| `metadata` | object | Yes (may be `{}`) | Structured facts to filter on. Sanitised and redacted at emit time; a snapshot, not a reference to the caller's dict. |

### Reserved `metadata` keys

Written by the emitter, never by the caller. Query for them to find mis-instrumented
call sites before the next incident does.

| Key | Meaning |
|---|---|
| `_invalid_severity` | The raw severity value that could not be resolved. Event filed at `ERROR`. |
| `_unknown_event_type` | `event_type` is not an `EventType` member. |
| `_metadata` | `metadata` was not a mapping; its content is preserved here rather than dropped. |
| `_serialization_error` | The record could not be encoded; a degraded placeholder went to the sink. |

### Sanitisation rules applied to `metadata`

| Input | Result | Why |
|---|---|---|
| Key whose lowercased form is in `redact_keys` | `"[REDACTED]"` | Exact whole-key match, so `token_bucket_size` is not mistaken for a credential. |
| Non-string dict key | Key coerced with `str()` | `json.dumps` raises `TypeError` on a non-string key; `default=` covers values only. |
| Circular reference | `"<circular-reference>"` | `json.dumps` raises `ValueError`. |
| `NaN` / `±Infinity` | Their `repr` as a string | `json.dumps` emits the bare tokens `NaN` / `Infinity`, which are **not valid JSON** (RFC 8259 §6 admits no such literals). A strict consumer rejects the whole line. |
| Nesting deeper than 8 | `"<max-depth-exceeded>"` | Bounds the walk. |
| Any other object | `repr`, capped at 512 chars | Bounds record size on a per-order event. |

---

## 2. Identifier and log-format standards

| Standard | What it says | How this skill applies it |
|---|---|---|
| **W3C Trace Context**, Recommendation, `trace-id` field | 16-byte value represented as 32 lowercase hex characters; all-zeroes is invalid; the value SHOULD be globally unique and a randomly generated value SHOULD be preferred over other unique-ID algorithms. | `new_correlation_id()` returns `uuid.uuid4().hex` — exactly that shape and entropy, so the ID can be carried into a tracing system unchanged. A truncated ID would collide and silently merge two order lifecycles. |
| **OpenTelemetry Logs Data Model** (specification) | `Timestamp` is `uint64` nanoseconds since the Unix epoch. `SeverityNumber` ranges: TRACE 1–4, DEBUG 5–8, INFO 9–12, WARN 13–16, ERROR 17–20, FATAL 21–24; larger is more severe. | `ts_ns` is that integer. `severity_number` uses the base of each range (5/9/13/17/21). `correlation_id` maps to `TraceId`, `component` to a resource attribute. |
| **RFC 8259** (JSON) | The grammar defines no `NaN`, `Infinity`, or `-Infinity` literals. | `to_json` runs with `allow_nan=False`, and sanitisation removes non-finite floats first, so an invalid line is impossible rather than merely unlikely. |
| **RFC 3339** (date/time on the internet) | Profile of ISO 8601 for internet timestamps, `Z` for UTC. | `ts_iso` format, nine fractional digits. |

---

## 3. Recordkeeping regimes that commonly govern these records

Applicability is entity- and jurisdiction-specific. Nothing in `structured_logger.py`
enforces any of the below — retention, immutability, and access control are properties of
the sink and the storage behind it.

| Jurisdiction | Rule | What it requires | Relevance |
|---|---|---|---|
| **US (SEC)** | **Rule 17a-4(f)**, as amended by Exchange Act Release No. 34-96034 (adopted 12 Oct 2022), *Electronic Recordkeeping Requirements for Broker-Dealers, Security-Based Swap Dealers, and Major Security-Based Swap Participants* | Adds an **audit-trail alternative** to the long-standing WORM requirement: a system may instead maintain a complete time-stamped audit trail covering all modifications to and deletions of a record, the date and time of operator entries and actions that create, modify or delete a record, the individual(s) responsible, and any other information needed to maintain the audit trail. The amendments also removed the pre-use notification to the designated examining authority and allow a "designated executive officer" as an alternative to a third-party undertaking. | Binds **registered broker-dealers**, not their customers. Where it applies, the choice of WORM vs. audit-trail is a storage-layer decision this module does not make. Its direct consequence for schema design: under either mode a record cannot be quietly edited out later, so **a credential that reaches a record is unremovable for the retention period** — hence redaction before serialisation. |
| **US (SEC)** | **Rule 17a-4(a), (b)** | Blotters, ledgers and certain account records: **6 years**. Most other required records including business communications: **3 years, the first 2 in an easily accessible place**. | Sets the horizon the sink must survive, and therefore why every record carries `schema_version`. |
| **US (FINRA)** | **Rule 4511(b), (c)** | Books and records required by FINRA rules must be preserved in a format and media complying with SEA Rule 17a-4; where no retention period is specified by FINRA or the Exchange Act rules, preserve for **at least 6 years**. | The default horizon for a member firm's records that no more specific rule covers. |
| **US (FINRA)** | **Rule 4590** (relocated from the OATS rule set; tolerance reduced by SR-FINRA-2016-005, SEC approval 8 Apr 2016) | Computer system clocks recording events in **NMS securities (including standardized options) and OTC Equity Securities** must be synchronised to within **50 milliseconds** of the NIST atomic clock; other business clocks to within **1 second**. Members must document and maintain clock-synchronisation procedures and keep a **log of synchronisation times and results**. | Bounds what `ts_ns` can mean. The 50 ms tolerance is also why `seq`, not the timestamp, is this skill's ordering field: sub-50 ms event ordering is not recoverable from a compliant clock alone. |
| **EU (MiFID II)** | **RTS 6**, Commission Delegated Regulation (EU) **2017/589**, **Art. 28** — *Content and format of order records* | A firm engaging in a **high-frequency algorithmic trading technique** must record the details of each submitted order **immediately after submission**, in the format of Tables 2 and 3 of Annex II, keep that information updated, and **retain the records for five years** from the date the order was submitted to a trading venue or to another investment firm for execution. | Applies to HFT-technique firms under MiFID II. Note the scope boundary: Art. 28 order records have a **prescribed** schema; this skill's schema is for application forensics and is **not** a substitute for them. The two coexist. |
| **EU (MiFID II)** | **RTS 25**, Commission Delegated Regulation (EU) **2017/574**, Annex Table 2 | Business clocks must be synchronised to UTC. For activity using a **high-frequency algorithmic trading technique**: maximum divergence from UTC **100 microseconds**, timestamp granularity **1 microsecond or better**. Other members/participants: coarser tiers, down to 1 millisecond granularity where gateway-to-gateway latency exceeds 1 ms. | The reason `ts_ns` is an integer nanosecond field: a float epoch-seconds field is a poor carrier for a 1 µs granularity obligation, and there is no reason to accept the loss. |
| **India (SEBI)** | Circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013**, 4 Feb 2025, *Safer participation of retail investors in Algorithmic trading* (implementation deferred to 1 Oct 2025 by the circular dated 29 Jul 2025) | Establishes broker responsibilities for retail algo participation, including an **audit trail built on a unique identifier** tagging algo orders, and exchange permission before offering algo services. | Where this applies, the exchange-assigned algo identifier should be carried in `metadata` on every order-lifecycle event so the internal correlation ID and the regulatory tag join. **The full circular text could not be retrieved for this reference; read the circular and the exchange implementation notes for the operative clauses, tagging format and retention specifics rather than relying on this summary.** |

### What none of these say

No regime cited above prescribes an application log schema, a correlation-ID format, a
severity vocabulary, a JSON field name, or an in-memory buffer size. Those are
engineering choices, made here against the OpenTelemetry and W3C specifications in
section 2 rather than against any regulation. Do not present them as compliance
requirements.

---

## 4. Sources consulted

- W3C, *Trace Context*, `trace-id` field — https://www.w3.org/TR/trace-context/
- OpenTelemetry, *Logs Data Model* — https://opentelemetry.io/docs/specs/otel/logs/data-model/
- SEC, *Electronic Recordkeeping Requirements for Broker-Dealers…*, Release 34-96034 (12 Oct 2022) — https://www.sec.gov/files/rules/final/2022/34-96034.pdf; staff FAQ — https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/rule-amendments-broker
- FINRA, *Rule 4511 — General Requirements* — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511
- FINRA, *Rule 4590 — Synchronization of Member Business Clocks* — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4590; *Regulatory Notice 16-23* — https://www.finra.org/rules-guidance/notices/16-23
- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
- Commission Delegated Regulation (EU) 2017/574 (RTS 25) — https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0574
- SEBI, *Safer participation of retail investors in Algorithmic trading* (4 Feb 2025) — https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
