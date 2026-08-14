# Pre-Flight Checklist: Deprecation Monitoring

Use this checklist before merging broker adapter changes into production.

## Wiring

- [ ] **Header hook installed**: `BrokerDeprecationMonitor.inspect_http_headers` runs
      from the HTTP client's global response handler, so it sees every response and not
      just the endpoints someone remembered to instrument.
- [ ] **Changelog poller scheduled**: a cron/Celery/worker job invokes
      `parse_changelog_entry` at least daily, per broker.
- [ ] **WebSocket path considered**: feed deprecations arrive in control frames or the
      changelog, never in a REST response header.
- [ ] **Alert callback wired** to the firm's router, and **cheap** — it is invoked while
      the registry lock is held, so it must enqueue rather than make a blocking call.

## Correctness

- [ ] **Clock is timezone-aware**: `now_fn` returns an aware UTC datetime. A naive clock
      is interpreted as UTC and logs a warning — confirm that warning is absent in
      staging rather than relying on the fallback.
- [ ] **Escalation thresholds match your release cadence**: the 7/30-day defaults are
      operational conventions, not RFC requirements. A desk whose migration work takes
      six weeks should raise `warning_days`.
- [ ] **Boundary behaviour confirmed**: a sunset 23 hours out reports
      `CRITICAL_SUNSET_IMMINENT` with `days_remaining == 0`, not `EXPIRED`.
- [ ] **Both header contracts covered**: RFC 9745 `Deprecation: @<unix-seconds>` parses
      into a date, and the legacy `Deprecation: true` still raises an undated notice.
- [ ] **Link extraction tested**, including a target containing a comma and a
      multi-valued `rel="sunset alternate"`.

## Failure containment

- [ ] **The monitor cannot break the trading path**: verify with `None` headers, a
      non-mapping argument, and `{"Sunset": None}` that no exception reaches the caller
      and no bogus notice is produced. This is a property of the code, not a hope — the
      header hook runs on the same thread as live order submissions.
- [ ] **Alert callback failures are contained**: a raising callback is logged with a
      traceback, and the notice stays registered so it re-alerts on escalation.
- [ ] **Poll-loop failures are contained**: an exception parsing one changelog entry
      does not stop the poller. Confirm something alerts if the poller goes quiet —
      a silent monitor and a clean broker look identical from the outside.

## Known limits accepted

- [ ] **Header absence proves nothing.** RFC 8594 timestamps are hints and adoption is
      inconsistent; the changelog path, not the header path, is the primary channel for
      most brokers.
- [ ] **Changelog dates are heuristic.** Ambiguous numeric dates (`11/12/2026`) are
      deliberately not parsed, and multi-candidate entries are flagged for human
      confirmation. Nothing here should auto-disable a strategy unreviewed.
- [ ] **The registry is per-process and in-memory.** It does not survive a restart and
      is not shared across replicas; expect re-alerting after a deploy.
