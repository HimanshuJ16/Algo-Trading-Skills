---
name: broker-status-page-monitoring-integration
description: >-
  Use when an order or feed failure raises the on-call question of whether the broker is
  down or your code is. Polls Atlassian Statuspage v2 summary feeds scoped to the
  components your order flow uses, and requires fresh positive evidence.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, status-page, outage-monitoring, incident-response, statuspage-io, health-checks
  brokers_frameworks: "Atlassian Statuspage Status API v2; Alpaca Status Page; Coinbase Status Page"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when an order submission fails, a WebSocket drops, or a REST call
times out, and the on-call question is *is this the broker or is it us?* Answering it
by hand costs minutes at exactly the wrong moment. This skill polls the broker's
public Statuspage v2 feed, scopes the reading to the components your order flow
actually uses, and returns a classification an incident pipeline can route on.

The two wrong answers are expensive in opposite directions, and the whole design
follows from that asymmetry:

- Wrongly answering **"broker"** suppresses the ticket for a live bug in your own code
  and, in most deployments, trips a circuit breaker. The bug stays in production
  because the pager never fired.
- Wrongly answering **"us"** sends an engineer to debug working code during a broker
  incident, when their attention is worth the most.

So **each confident verdict requires positive evidence.** `EXTERNAL_BROKER_OUTAGE`
requires fresh evidence of impairment; `INTERNAL_APPLICATION_BUG` requires fresh
evidence of health. Everything else — no reading, a stale reading, an unreachable
feed, an unrecognised value, or a merely *degraded* page — returns `UNKNOWN_FAILURE`,
which routes a human to the incident instead of resolving it automatically in either
direction.

## When NOT to Use

- **As an outage detector.** A status page is a human-published artifact, updated after
  the broker's ops team notices, triages and decides to disclose. `OPERATIONAL` means
  "nothing has been published", never "nothing is wrong". Pair it with a first-party
  signal — reject rates, heartbeat latency, disconnect counts — and treat this as the
  corroborating input, not the trigger.
- **As the circuit breaker.** This classifies; it does not halt trading, cancel orders
  or flatten positions. Wire the verdict into
  `kill-switch-and-drawdown-circuit-breakers`, and route around the broker with
  `broker-failover-secondary-account-routing`.
- **For scheduled API retirements.** A sunset date is a planned deprecation, not an
  unplanned outage — see `broker-api-deprecation-notice-monitoring`.
- **For brokers without a Statuspage feed.** The parser is specific to the Atlassian
  Statuspage v2 schema. A broker publishing status as an HTML page, an RSS feed or a
  Twitter account needs a different ingestion path; do not point this at an arbitrary
  URL and assume the absence of a `status` object means healthy.
- **As a retry gate on an ambiguous order state.** "The broker is up" is not evidence
  that your timed-out order was *not* accepted. That is
  `order-placement-idempotency`, and this skill's verdict must never be used to
  justify a blind resubmission.

## Prerequisites

- Each broker's Statuspage v2 summary URL — the **full** URL including
  `/api/v2/summary.json`, e.g. `https://status.alpaca.markets/api/v2/summary.json`.
  These are public and unauthenticated; private and trial pages require an API key in
  an `Authorization` header.
- **The exact component names your order flow depends on**, copied from the feed's
  `components[].name`. Without these the monitor can only reason over the blended
  page-wide indicator, which is both too noisy (an unrelated minor incident) and too
  quiet (a dead trading component under a green page).
- An HTTP transport that **enforces a connect and read timeout**. The diagnosis path
  runs inside an order-failure handler; an untimed socket there converts a failed order
  into a hung strategy.
- A poll interval. 60s is a reasonable default; polling faster than ~10s is wasted work
  — see Common Pitfalls.

## Workflow

1. **Ingest `summary.json`.** GET the full summary URL. The response carries `page`,
   `status`, `components`, `incidents` and `scheduled_maintenances`. Treat a non-200,
   a transport exception, or a payload without a `status` object as *no evidence* —
   never as an all-clear.

2. **Map `status.indicator`, and refuse to guess.** `none` → OPERATIONAL, `minor` →
   DEGRADED, `major`/`critical` → MAJOR_OUTAGE, `maintenance` → MAINTENANCE. Anything
   else maps to UNKNOWN. Do **not** default unrecognised strings to the worst case: a
   typo or a future enum value would then trigger an automatic trading halt *and* an
   automatic ticket suppression off a string nobody validated.

3. **Scope to the components you depend on.** Match your declared component names
   against `components[].name` and reduce their statuses to the worst one
   (`partial_outage` counts as an outage; a missing or unrecognised status counts as
   UNKNOWN, never as operational). This component-scoped state overrides the page
   indicator when it is known — it is what catches a dead order-routing component
   under a green page, and what stops an incident on a component you never call from
   silencing your bug tickets. If a declared name matches nothing, alert on it: the
   broker renamed a component and your scoping is now silently off.

4. **Check freshness before classifying.** A reading older than the configured bound
   (default 300s) is not evidence. Measure age from your own fetch clock — **not** from
   `page.updated_at`, which is a last-*changed* timestamp: both feeds verified for this
   skill carried a healthy `page.updated_at` several hours old.

5. **Classify, in this order.**

   | Evidence | Verdict |
   |---|---|
   | No reading, or older than the freshness bound | `UNKNOWN_FAILURE` |
   | A declared dependency component is impaired | `EXTERNAL_BROKER_OUTAGE` |
   | Page impaired, but every dependency reads operational | `UNKNOWN_FAILURE` |
   | Page impaired, no dependency evidence | `EXTERNAL_BROKER_OUTAGE` |
   | Anything merely degraded, or unrecognised | `UNKNOWN_FAILURE` |
   | Page operational | `INTERNAL_APPLICATION_BUG` |

6. **Route the verdict.** `EXTERNAL_BROKER_OUTAGE` suppresses the code-bug escalation
   and trips the circuit breaker. `INTERNAL_APPLICATION_BUG` pages the owning engineer.
   `UNKNOWN_FAILURE` pages a human and suppresses nothing — it is the correct answer
   far more often than a two-way classifier admits.

> Full procedure, decision-table rationale and the EMS/alerting contract: see
> `references/workflows.md`.
> Endpoint evidence, schema and verified vendor claims: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating "I could not reach the status page" as "the broker is fine".** This is the
  single worst failure mode here, because it fires exactly when the broker is in
  trouble. An unreachable feed, an HTTP 500, a missing transport or an unconfigured
  broker key are all *absence of evidence*, and must classify as undetermined — never
  as an operational platform.
- **Suppressing a bug ticket on a `minor` indicator.** `minor` is the page-wide roll-up
  of *any* small published incident — a degraded docs site, a slow web dashboard. It is
  consistent with both a broker fault and your own bug, so it justifies neither verdict.
  Suppressing on `minor` means a real bug in your order construction goes unexamined
  for as long as an unrelated incident stays open.
- **Trusting a green page you fetched from a CDN.** Both verified endpoints serve
  `Cache-Control: max-age=10, public, s-maxage=10, stale-while-revalidate=20,
  stale-if-error=3600`. That `stale-if-error=3600` means that when Statuspage's own
  origin fails, the edge may keep serving the last good body — a green "All Systems
  Operational" — for up to an hour.
- **Using `page.updated_at` as a freshness check.** It records when the page last
  *changed*, not when it was last confirmed. A healthy page that has not changed in
  days carries a days-old timestamp; treating that as staleness turns every quiet
  period into an alert. Measure age from your own fetch instead.
- **Reading the page indicator and stopping there.** `status.indicator` is a blend
  across every component on the page. Alpaca publishes 112 components in 11 groups,
  Coinbase 172 in 7 — an outage confined to "Broker API (Sandbox)" while you trade live
  is invisible in your decision if you only scope to the components you use, and an
  outage confined to *your* component can sit under a page-wide `none`.
- **Double-counting component groups.** Group entries (`"group": true`) appear in the
  same `components` array as their children and carry a rolled-up status, so a single
  failing child reports as two impaired components unless groups are separated out.
- **Reading a missing component `status` as operational.** A `status` field that is
  absent, null or an unrecognised string is unreadable, not healthy. Folding it into an
  all-clear is how a schema change becomes a silent mis-diagnosis.
- **Polling faster to detect outages sooner.** The public Status API is documented as
  *not* rate limited, so the risk is not an IP block — it is that `max-age=10` means a
  refetch inside 10 seconds returns the identical cached body. Sub-cache polling buys no
  freshness. (The separate, token-authenticated **Manage** API *is* limited — 60 req/min
  per page, 1 req/s per token, `429` plus `Retry-After` — but that is not the API you
  poll here.)
- **Fetching the status page once per failed order.** A broker incident produces a burst
  of failures, and a diagnoser that refreshes on each one issues a fetch storm and adds
  network latency to every failure handler. Bound the implicit refresh to the cache TTL.
- **Blocking the order path on an untimed HTTP call.** The diagnosis runs inside an
  exception handler on the trading thread. Without a transport timeout, one hung status
  fetch stalls the strategy that was already failing.
- **Letting the diagnoser raise.** An exception thrown while classifying an execution
  failure displaces the original trading exception and destroys the forensic trail.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-status-page-monitoring-integration/scripts`
- Assert an unreachable feed (HTTP 500, transport exception, missing transport,
  unknown broker key) yields `UNKNOWN_FAILURE` with `platform_state` UNKNOWN — never
  `INTERNAL_APPLICATION_BUG` and never OPERATIONAL. This is the highest-value single
  assertion in the suite.
- Assert a `minor` indicator yields `UNKNOWN_FAILURE`, not `EXTERNAL_BROKER_OUTAGE`.
- Assert an unrecognised `status.indicator` yields UNKNOWN, not MAJOR_OUTAGE.
- Assert a dependency component in `major_outage` under a page indicator of `none`
  still yields `EXTERNAL_BROKER_OUTAGE`.
- Assert a `critical` page with all declared dependencies operational yields
  `UNKNOWN_FAILURE` rather than suppressing the ticket.
- Assert a reading older than the freshness bound is refetched, and yields
  `UNKNOWN_FAILURE` when the refetch also fails.
- Assert a component whose `status` key is absent does not count toward an all-clear.
- Assert a group and its failing child are not both listed as affected components.
- Assert a burst of 50 diagnoses issues one status fetch, not 50.
- Smoke-test against the live feeds with a timeout-enforcing transport and confirm your
  declared component names appear in `unmatched_dependencies` as an empty list.

## Related Skills

- `broker-failover-secondary-account-routing`
- `kill-switch-and-drawdown-circuit-breakers`
- `broker-api-deprecation-notice-monitoring`
- `circuit-breaker-for-downstream-service-calls`
- `structured-logging-for-post-incident-forensics`
- `order-placement-idempotency`
- `vendor-outage-fallback-data-source-hierarchy`
