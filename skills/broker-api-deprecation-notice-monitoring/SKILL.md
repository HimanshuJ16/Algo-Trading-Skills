---
name: broker-api-deprecation-notice-monitoring
description: Use when building production broker adapters to detect impending API endpoint
  retirements — parsing RFC 8594 Sunset headers, RFC 9745 Deprecation headers, RFC 8288
  sunset/deprecation Link relations, and developer changelog feeds — so ops teams get a
  dated migration deadline before a live trading bot starts calling an endpoint that no
  longer answers.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- deprecation-monitoring
- sunset-headers
- rfc-8594
- rfc-9745
- changelog-parser
- api-maintenance
brokers_frameworks:
- RFC 8594 (Sunset header)
- RFC 9745 (Deprecation header)
- RFC 8288 (Web Linking)
- Python Requests
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating long-running algorithmic trading systems against
REST/WebSocket broker APIs. Brokers retire legacy endpoints and change payload schemas,
and the announcement usually arrives weeks ahead — in a developer changelog, and
sometimes as an RFC 8594 `Sunset` or RFC 9745 `Deprecation` response header. This skill
turns those scattered signals into a dated, de-duplicated migration deadline with an
escalation tier attached.

The monitor is a **deadline detector**, and its failure modes are asymmetric: a false
positive costs an engineer a few minutes reading a changelog entry, a false negative
means a bot keeps calling an endpoint that stops answering mid-session. Every
classification decision below is biased accordingly.

## When NOT to Use

- **As proof that nothing is being retired.** Header adoption is inconsistent across
  brokers, and RFC 8594 Section 3 is explicit that `Sunset` timestamps are hints:
  "it is not guaranteed that the resource will, in fact, be available until that time
  and will not be available after that time." An empty registry means nothing was
  *signalled*, not that nothing is changing. Alpaca's Market Data API v1 retirement,
  for instance, was announced in a blog post, not a header.
- **As the migration itself.** This detects and dates a deadline. Executing the version
  cutover belongs to `broker-api-versioning-migration-playbook`; detecting schema-level
  breakage belongs to `broker-api-changelog-diffing-tool`.
- **For live outage detection.** A sunset date is a scheduled retirement, not an
  unplanned outage. Use `broker-status-page-monitoring-integration`.
- **As an authoritative parse of changelog prose.** The changelog path is a
  keyword-and-date heuristic that routes a human to the entry. Do not auto-disable a
  strategy on its output alone.

## Prerequisites

- A broker API adapter that exposes HTTP response headers to a hook or middleware layer.
- The broker's developer changelog RSS/Atom/JSON feed URL, and a scheduler to poll it.
- A structured logging pipeline or alert router (PagerDuty, Slack, Opsgenie).

## Workflow

1. **Inspect live response headers.** Hook `inspect_http_headers` into the HTTP client's
   global response handler and check every response for `Sunset` (RFC 8594),
   `Deprecation` (RFC 9745), `Link` with `rel="sunset"` or `rel="deprecation"`
   (RFC 8288), and the non-standard `X-API-Deprecation-Warning`. This hook runs on the
   same thread as live orders, so it must never raise into the caller — contain
   unexpected failures, log them with a traceback, and return no notice.

2. **Parse the two headers by their different contracts.** `Sunset` is an HTTP-date, so
   accept all three HTTP-date forms (IMF-fixdate, obsolete RFC 850, asctime). RFC 9745
   `Deprecation` is a Structured Field Date — `@1688169599`, seconds since the epoch —
   but deployed gateways still emit the pre-RFC `Deprecation: true`, which asserts
   deprecation without dating it. Treat an unparseable `Deprecation` value as
   "deprecated, date unknown", never as "not deprecated".

3. **Keep deprecation and sunset distinct.** Deprecation is when the broker stops
   recommending the endpoint; sunset is when it stops answering. Derive urgency only
   from the sunset date. If `Sunset` is earlier than `Deprecation`, the broker's own
   metadata violates RFC 9745 Section 4 — log it and confirm the dates before planning
   a migration around either.

4. **Poll the developer changelog.** Match deprecation keywords, then extract every
   unambiguous date. Discard candidates that fall before the entry's publication date —
   those are publication dates and release history, not deadlines — and take the
   **earliest** of what survives, because a later date in the same entry is usually the
   support window for the *replacement* API. Do not parse locale-ambiguous numeric dates
   (`11/12/2026`): day-first and month-first are indistinguishable without knowing the
   publisher, and guessing wrong moves a deadline by up to eleven months.

5. **Classify urgency from a single clock reading.** With $D$ = whole days remaining,
   floored, computed in UTC:
   - Sunset instant already passed → `EXPIRED`.
   - $D \le 7$ → `CRITICAL_SUNSET_IMMINENT`.
   - $7 < D \le 30$ → `WARNING_30_DAYS`.
   - $D > 30$, or no sunset date at all → `NOTICE`.

   Expiry is decided by comparing instants, **not** by testing whether the floored day
   count reached zero. A sunset 23 hours away has zero whole days left and has not
   expired; reporting it as `EXPIRED` tells the desk the migration window is gone while
   a final day of it remains. The 7/30 day thresholds are operational conventions for
   migration lead time — neither RFC mandates them — so they are configurable.

6. **De-duplicate, then route.** Key the registry by broker, endpoint, and entry
   identity; alert on a new notice, an escalation, or a broker moving the sunset date,
   and suppress pure de-escalation so routine re-inspection does not page anyone.
   Route `NOTICE` to logs, `WARNING_30_DAYS` to Slack/Teams, and
   `CRITICAL_SUNSET_IMMINENT`/`EXPIRED` to the on-call rota.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Crashing the request path with the monitor.** A header hook runs inline with every
  live order and quote request. Wiring `now_fn=datetime.datetime.now` (naive) makes the
  sunset subtraction raise `TypeError` on the first deprecated endpoint — converting
  "this breaks in 30 days" into "this breaks now". Interpret naive clocks as UTC, and
  contain unexpected errors rather than propagating them.
- **Taking the first date found in a changelog entry.** Entries lead with their
  publication date and often cite the replacement API's support window. Picking the
  first match dates the deadline to the announcement; picking the latest dates it to the
  replacement's end of life. Either way a real three-week deadline can be filed as
  `NOTICE` or as already-`EXPIRED` — a silent miss in the one place this skill exists to
  prevent one.
- **Reading `days_remaining == 0` as expired.** `timedelta.days` floors, so a sunset
  0.96 days away reports 0. Compare instants for expiry and keep the floored count for
  display only.
- **Collapsing every changelog notice onto one key.** Keying feed notices by
  `broker:GLOBAL_FEED` means each new announcement evicts the previous one, so a broker
  retiring three endpoints shows one outstanding deprecation.
- **Assuming `Deprecation: true` is still the standard.** RFC 9745 (Standards Track,
  March 2025) requires a Structured Field Date; a parser written only for the boolean
  form silently drops the date the broker actually supplied.
- **Splitting a `Link` header on commas.** Commas are legal inside a link target, so
  `<https://x/docs?tags=v1,legacy>; rel="sunset"` splits into two fragments and the
  migration URL is lost. Match `<target>` forms directly, and compare `rel` as a whole
  token — RFC 8288 allows several space-separated relation types in one parameter.
- **Timing the countdown twice.** Computing `days_remaining` and the urgency tier from
  two separate clock readings lets them straddle a threshold and disagree.
- **Monitoring REST only.** WebSocket feeds are retired too, and their deprecation
  notices arrive in control frames or the changelog, never in a REST response header.

## Verification

- Run `python -m unittest discover -s skills/broker-api-deprecation-notice-monitoring/scripts`
  and confirm all tests pass.
- Feed a mock response carrying `Sunset: Wed, 25 Nov 2026 00:00:00 GMT` against a frozen
  clock and confirm the extracted UTC date, the day count, and the escalation tier.
- Confirm the boundary cases explicitly: a sunset 23 hours out is `CRITICAL`, not
  `EXPIRED`; exactly 7 days is `CRITICAL`; exactly 30 days is `WARNING_30_DAYS`.
- Feed a changelog entry whose body restates its publication date before naming a later
  sunset date, and confirm the *later* date is chosen.
- Hand the header inspector `None`, a list, and `{"Sunset": None}`, and confirm it
  returns no notice and raises nothing.

## Related Skills

- `broker-api-versioning-migration-playbook`
- `broker-api-changelog-diffing-tool`
- `broker-status-page-monitoring-integration`
- `structured-logging-for-post-incident-forensics`
