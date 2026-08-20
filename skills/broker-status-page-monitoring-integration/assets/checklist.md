# Pre-Flight / Sign-off Checklist — broker-status-page-monitoring-integration

Use this before wiring the monitor's verdicts into live alerting or a circuit breaker.

## Feed configuration

- [ ] **Full URLs registered.** Each broker's value is the complete
      `https://status.<broker>/api/v2/summary.json`, not a base URL that something else
      appends to.
- [ ] **Schema confirmed.** Each registered URL returns HTTP 200 with a top-level
      `status` object. A broker that does not publish an Atlassian Statuspage needs a
      different ingestion path, not this one.
- [ ] **Dependency components declared.** For every broker, the exact
      `components[].name` values your order flow depends on are configured — copied
      from the live feed, capitalisation included.
- [ ] **Unmatched dependencies alert.** A declared component name that matches nothing
      raises an alert, not just a log line. It means the broker renamed a component and
      component-scoped diagnosis has silently degraded to page-wide reasoning.
- [ ] **Transport enforces a timeout.** Connect *and* read. The diagnosis path runs on
      the trading thread inside an order-failure handler.
- [ ] **Poll interval is 30–60s.** Faster buys nothing: the endpoints serve
      `max-age=10`, so a sub-TTL refetch returns the identical cached body.
- [ ] **Secrets handled.** If any registered page is private or on trial, its API key is
      loaded from the deployment's secret store, not from the monitor's config file.

## Classification correctness

- [ ] **Unreachable feed is UNKNOWN.** HTTP 500, transport exception, missing transport
      and unknown broker key all yield `UNKNOWN_FAILURE` with platform state UNKNOWN —
      never `INTERNAL_APPLICATION_BUG`, never OPERATIONAL.
- [ ] **`minor` does not suppress.** A `minor` page indicator yields `UNKNOWN_FAILURE`,
      so an unrelated small incident cannot silence a real bug ticket.
- [ ] **Unrecognised indicator is UNKNOWN.** Not MAJOR_OUTAGE — an unvalidated string
      must not halt trading and suppress alerting at the same time.
- [ ] **Component scope overrides the page.** A dependency component in `major_outage`
      under a page indicator of `none` still yields `EXTERNAL_BROKER_OUTAGE`.
- [ ] **Healthy dependencies under a sick page are undetermined.** A `critical` page
      with every declared dependency operational yields `UNKNOWN_FAILURE`.
- [ ] **Missing component status is not healthy.** A component whose `status` is absent,
      null or unrecognised does not contribute to an all-clear.
- [ ] **Groups are not double-counted.** A failing child and its `group: true` parent do
      not both appear in the affected-component list.

## Freshness and load

- [ ] **Freshness bound enforced.** A reading older than the configured bound is
      refetched, and yields `UNKNOWN_FAILURE` if the refetch also fails.
- [ ] **Freshness is measured locally.** `page.updated_at` is captured for forensics
      only — it is a last-*changed* timestamp and is hours old on healthy pages.
- [ ] **A failed fetch preserves the last good reading.** One 503 does not discard a
      reading taken seconds earlier.
- [ ] **Failure bursts do not stampede.** 50 consecutive failed orders issue one status
      fetch, not 50.

## Routing and operations

- [ ] **The monitor does not halt trading itself.** The verdict is routed to the
      circuit-breaker and failover skills; classification and enforcement stay separate.
- [ ] **`UNKNOWN_FAILURE` pages a human.** It is never auto-resolved, auto-suppressed,
      or used to justify an order retry.
- [ ] **Verdicts are corroborated.** `INTERNAL_APPLICATION_BUG` is cross-checked against
      first-party reject rates and latency before an engineer is woken; an operational
      status page is not proof.
- [ ] **A green reading is never treated as proof.** `stale-if-error=3600` lets the CDN
      serve a cached "All Systems Operational" for up to an hour after Statuspage's own
      origin fails.
- [ ] **The diagnoser never raises.** Verified that a misconfigured monitor returns
      `UNKNOWN_FAILURE` rather than throwing into the order-failure handler.
- [ ] **Evidence is logged with the verdict.** Indicator, page state, dependency state,
      affected components and evidence age are recorded on every classification, for
      post-incident review.

## Automated testing

- [ ] **Unit suite passes:**
      `python -m unittest discover -s skills/broker-status-page-monitoring-integration/scripts`
- [ ] **Live smoke test run** against each registered feed with a timeout-enforcing
      transport, confirming an empty `unmatched_dependencies` list.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
