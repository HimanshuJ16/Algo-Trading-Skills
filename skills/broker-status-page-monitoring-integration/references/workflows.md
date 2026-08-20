# Deep Workflow Reference — broker-status-page-monitoring-integration

This file holds the full technical procedure referenced by `SKILL.md`.

## The invariant everything else follows from

A two-way classifier — "broker" or "us" — is the wrong shape for this problem, because
the evidence is frequently insufficient to support either answer. The monitor therefore
has three verdicts, and the two confident ones each require **positive, fresh
evidence**:

| Verdict | Requires | Downstream effect |
|---|---|---|
| `EXTERNAL_BROKER_OUTAGE` | Fresh evidence the broker is impaired | Suppress the code-bug escalation; trip the circuit breaker |
| `INTERNAL_APPLICATION_BUG` | Fresh evidence the broker is healthy | Page the owning engineer |
| `UNKNOWN_FAILURE` | *default* | Page a human; suppress nothing, file nothing automatically |

`UNKNOWN_FAILURE` is not a failure of the monitor. It is the correct answer whenever
the feed is unreachable, stale, unparseable, carrying a value the parser does not
recognise, or reporting a state that is genuinely consistent with both causes. A
classifier that always picks a side is a classifier that is confidently wrong on the
cases that matter.

## Full procedure

### 1. Ingest `summary.json`

GET the **full** summary URL — including `/api/v2/summary.json`. Do not build it by
concatenating a base URL with `summary.json`; the configured value is already complete.

The transport must enforce a **connect and read timeout**. This code path runs inside
an order-failure handler on the trading thread, and an untimed socket there turns a
failed order into a hung strategy.

Any of the following is *no evidence*, and must produce an UNKNOWN reading rather than
an all-clear:

- a non-200 status code;
- a transport exception (DNS, TLS, connection reset, timeout);
- a body that is not a JSON object, or that lacks a `status` object.

A failed fetch must **not** overwrite the last successful reading. During an incident
the status page is itself under load, and discarding a 20-second-old good reading
because of one 503 throws away the best evidence available. Staleness is enforced
separately, when the reading is read.

### 2. Map the page indicator

`none` → OPERATIONAL, `minor` → DEGRADED, `major`/`critical` → MAJOR_OUTAGE,
`maintenance` → MAINTENANCE, **anything else → UNKNOWN**.

The catch-all direction matters more than it looks. Defaulting an unrecognised string
to the worst case sounds conservative, but the verdict it produces
(`EXTERNAL_BROKER_OUTAGE`) both halts trading *and* suppresses bug tickets — so a typo
or a future enum value would silence your alerting off a string nobody validated.
Unknown means unknown.

### 3. Scope to your dependency components

Declare the component names your order flow actually uses, copied exactly from the
feed's `components[].name`, then:

- match case-insensitively against every component in the payload;
- map each match through the component status table in `references/standards.md`;
- reduce to the **worst** state, with one exception: if any match is UNKNOWN and
  nothing else is worse than OPERATIONAL, the result is UNKNOWN. An unreadable
  component cannot contribute to an all-clear;
- record every declared name that matched nothing, and **alert on it**. An unmatched
  name means the broker renamed a component and your scoping is now silently off —
  the monitor has quietly degraded to page-wide reasoning without telling anyone.

The component-scoped state overrides the page indicator whenever it is known. This is
what makes the two headline pitfalls tractable at once: a dead order-routing component
under a green page is caught, and an incident on a component you never call stops
silencing your bug tickets.

When separating affected components for reporting, keep `group: true` entries in their
own list. Groups carry a rolled-up status and sit in the same array as their children,
so a single failing child reports as two impairments otherwise.

### 4. Enforce freshness

A reading older than the configured bound (default 300s — an operational convention set
at a few multiples of a 60s poll, not a vendor requirement) stops being evidence.

Measure age from **your own fetch clock**. `page.updated_at` is a last-*changed*
timestamp: on 2026-08-20 both verified feeds carried a `page.updated_at` several hours
old on a page reading `"indicator": "none"`. Using it as a staleness bound turns every
quiet period into an alert.

When the cached reading is stale or absent, attempt one refresh — but bound the refresh
rate. A broker incident produces a burst of failures, and refreshing on each one
issues a fetch storm and adds network latency to every failure handler. The cache TTL
(`max-age=10`) is the natural floor: a refetch inside that window returns the identical
cached body anyway.

### 5. Classify

Applied in order:

| # | Evidence | Verdict |
|---|---|---|
| 1 | No reading, or older than the freshness bound | `UNKNOWN_FAILURE` |
| 2 | A declared dependency component is impaired (MAJOR_OUTAGE or MAINTENANCE) | `EXTERNAL_BROKER_OUTAGE` |
| 3 | Page impaired, but every declared dependency reads operational | `UNKNOWN_FAILURE` |
| 4 | Page impaired, no dependency evidence either way | `EXTERNAL_BROKER_OUTAGE` |
| 5 | Page or dependency merely DEGRADED | `UNKNOWN_FAILURE` |
| 6 | Page indicator unrecognised | `UNKNOWN_FAILURE` |
| 7 | Page operational, no impaired dependency | `INTERNAL_APPLICATION_BUG` |

Two rows deserve their rationale spelled out:

- **Row 3** is the case where the broker is visibly in a major incident but every
  component you actually use reads green. Suppressing your bug ticket would be
  unfounded — the incident is somewhere else. Filing one would be unfair to the
  engineer, since a page-wide incident often has effects the component grid does not
  yet reflect. Neither verdict is supported, so a human decides.
- **Row 5** is the fix for the most costly default in a naive implementation. A `minor`
  indicator is the roll-up of *any* small published incident — a degraded docs site, a
  slow dashboard. Treating it as a confirmed outage means a real bug in your order
  construction goes unexamined for as long as an unrelated incident stays open.

Maintenance is grouped with outage for the *verdict* because the cause is equally
external, but the platform state is reported separately so alerting can distinguish
planned work from an unplanned failure.

### 6. Never raise into the caller

The diagnosis runs inside an exception handler that is already holding a trading
exception. If the diagnoser throws — on a missing transport, an unknown broker key, a
malformed payload — it displaces the original exception and destroys the forensic
trail. Configuration errors are logged with a traceback and reported as
`UNKNOWN_FAILURE`.

The polling entry point is held to a different standard: it raises on configuration
errors (unknown broker, no transport), because those are bugs rather than incidents and
should be loud at startup.

### 7. Route the verdict

- `EXTERNAL_BROKER_OUTAGE` → suppress the code-bug escalation, tag the incident, and
  hand off to `kill-switch-and-drawdown-circuit-breakers` and
  `broker-failover-secondary-account-routing`. This skill classifies; it does not halt
  trading or move orders.
- `INTERNAL_APPLICATION_BUG` → page the owning engineer, and include the status
  evidence and its age in the ticket. The status page lags reality by minutes, so the
  verdict is a prior, not a proof — corroborate against first-party reject and latency
  metrics before closing the loop.
- `UNKNOWN_FAILURE` → page a human. Do not auto-resolve, do not auto-suppress, and do
  not retry the order on the strength of it. "The broker is up" is not evidence that
  your timed-out order was not accepted — that is `order-placement-idempotency`.

## Concurrency

A background poller and the trading thread touch the cached readings concurrently.
Guard them with a lock, and keep each reading immutable once published so a reader
never observes a half-updated view of the indicator, the component list and the
timestamp.

## Production Implementation Reference

- Reference code: `scripts/status_monitor.py` (`BrokerStatusPageMonitor`,
  `BrokerStatusSummary`, `FailureDiagnosisResult`, `BrokerPlatformState`,
  `IncidentDiagnosis`).
- Automated unit tests: `scripts/test_status_monitor.py`.
- The module performs no network I/O: the HTTP transport is injected as
  `http_fn(url) -> (status_code, decoded_json)` so the timeout policy, retry policy,
  proxy and TLS configuration stay under the deployment's control.
