# Workflow: Broker API Deprecation Monitoring

## 1. Real-time REST header extraction

Intercept API responses inside the HTTP client session (`requests` Session hooks,
`aiohttp` middleware, or an adapter wrapper).

- Check `Sunset`, `Deprecation`, `Link` (`rel="sunset"` and `rel="deprecation"`), and
  the vendor `X-API-Deprecation-Warning`.
- Parse `Sunset` as an HTTP-date via `email.utils.parsedate_tz`, which covers all three
  legal forms; accept ISO 8601 as a fallback for non-conforming gateways, honouring any
  offset rather than truncating to the date.
- Parse `Deprecation` as an RFC 9745 Structured Field Date (`@<unix-seconds>`). Accept
  the legacy `true` as an undated deprecation signal.
- Ignore header values that are not non-empty strings. Coercing a `None` value with
  `str()` yields the truthy literal `"None"` and manufactures a deprecation the broker
  never signalled.

**This hook runs on the trading thread.** It executes on every response, including
order submissions. It must not raise: contain unexpected exceptions, log them with a
traceback, and return no notice. A monitoring defect that propagates turns a scheduled
retirement into an immediate outage.

## 2. Asynchronous feed polling

Run a background thread, Celery worker, or cron job polling each broker's changelog
RSS/Atom/JSON feed roughly daily.

- Keyword-match the title and body: `deprecat*`, `sunset*`, `breaking change`,
  `end of life` / `end-of-life`, `retir*`.
- Extract **every** unambiguous date, not the first one: ISO `YYYY-MM-DD`,
  `20 November 2026`, and `November 20, 2026`. Skip purely numeric forms such as
  `11/12/2026`, which are locale-ambiguous.
- Discard candidates falling before the entry's publication day — those are publication
  dates and release history. Prefer dates strictly after publication; fall back to a
  date on the publication day so a same-day retirement notice is not dropped.
- Take the **earliest** surviving candidate. A later date in the same entry is usually
  the support window for the replacement API, and selecting it downgrades a near
  deadline to `NOTICE`.
- When more than one candidate survives, say so in the notice message so a human
  confirms against the source entry.

Wrap the poll body the same way as the header hook: an uncaught exception in a poll
loop silently stops all future polling, and nothing alerts on a monitor that has gone
quiet.

## 3. In-memory de-duplication

Maintain a registry guarded by a `threading.RLock`, keyed by
`broker_name : endpoint : entry_id`.

- The `entry_id` is what separates changelog notices, which all carry the endpoint
  `GLOBAL_FEED`. Use the entry permalink when the feed supplies one, otherwise a stable
  hash of broker, title, and publication date. Without it, each new announcement evicts
  the previous one.
- Fire the alert callback while holding the lock, so racing threads cannot dispatch the
  same notice twice. Callbacks must therefore be cheap — enqueue, do not make a
  synchronous HTTP call.
- Alert when the notice is new, when its urgency **escalates**, or when the broker
  moves the sunset date. Suppress pure de-escalation. Because the clock only advances,
  a notice with a fixed sunset date can only escalate, so the only route to
  de-escalation is a re-dated sunset — already covered by the date comparison.
- The registry is per-process and does not survive a restart. A restarted service
  re-alerts on everything it rediscovers; route accordingly if that matters.

## 4. Alert routing

- `NOTICE` — logs, for sprint planning. Also covers "deprecated, sunset date unknown".
- `WARNING_30_DAYS` — Slack/Teams, for engineering visibility.
- `CRITICAL_SUNSET_IMMINENT` — PagerDuty/Opsgenie. The endpoint is still live; the
  migration window is nearly closed.
- `EXPIRED` — PagerDuty/Opsgenie. The sunset instant has passed and calls may already
  be failing. Treat as an incident, not a planning item.
