# Broker & Framework Coverage — broker-status-page-monitoring-integration

## How to read this file

Everything below was verified against live endpoints and vendor documentation on
**2026-08-20**. Status pages are operated by the brokers, and both the URLs and the
component names change without notice — re-verify the component names before wiring
them into a dependency map, because a renamed component silently disables
component-scoped diagnosis.

## 1. The Atlassian Statuspage Status API v2

Every broker in the table below publishes through Atlassian Statuspage, so one parser
covers them all. The **Status API** is the per-page, public, unauthenticated
`/api/v2/*.json` surface. It is a different API from the **Manage API** at
`api.statuspage.io/v1` used to *update* a page, and they have different rate limits.

### Endpoints

| Endpoint | Contents |
|---|---|
| `/api/v2/summary.json` | Page-level indicator, all components, unresolved incidents, upcoming and in-progress maintenances. **This is the one to poll** — a single request covers the page roll-up and the component detail. |
| `/api/v2/status.json` | The page-level rollup alone: `indicator` plus a human description. |
| `/api/v2/components.json` | Components only. |
| `/api/v2/incidents/unresolved.json`, `/api/v2/incidents.json` | Unresolved, or the 50 most recent. |
| `/api/v2/scheduled-maintenances/{active,upcoming}.json` | Planned work. |

Source: [Atlassian Statuspage — Status API](https://metastatuspage.com/api).

### `status.indicator` — the page roll-up

Documented values: **`none`, `minor`, `major`, `critical`**, described as "a blended
component status" with human descriptions such as "All Systems Operational", "Partial
System Outage" and "Major Service Outage".

`maintenance` is **not** in that documented set, but it *is* a documented incident and
scheduled-maintenance `impact` value — Alpaca's live feed on 2026-08-20 carried a
scheduled maintenance with `"impact": "maintenance"` — and Statuspage blends impacts
into the page indicator. This skill therefore recognises `maintenance` explicitly and
maps **every other unrecognised value to UNKNOWN**. Treat the enum as open.

| Indicator | State | Rationale |
|---|---|---|
| `none` | `OPERATIONAL` | Nothing published. Not a positive assertion of health. |
| `minor` | `DEGRADED` | Ambiguous: consistent with both a broker fault and your bug. |
| `major` | `MAJOR_OUTAGE` | |
| `critical` | `MAJOR_OUTAGE` | |
| `maintenance` | `MAINTENANCE` | External and expected, rather than a failure. |
| *anything else* | `UNKNOWN` | Never guess a severity from an unvalidated string. |

Source: [Atlassian Statuspage — Status API](https://metastatuspage.com/api),
[GitHub Status — API](https://www.githubstatus.com/api).

### `components[].status` — the per-component detail

The public Status API documentation enumerates four values — `operational`,
`degraded_performance`, `partial_outage`, `major_outage`. A fifth,
**`under_maintenance`**, is settable through the Manage API and appears in live
payloads, so the parser recognises it and still treats the enum as open.

| Component status | State | Rationale |
|---|---|---|
| `operational` | `OPERATIONAL` | |
| `degraded_performance` | `DEGRADED` | |
| `partial_outage` | `MAJOR_OUTAGE` | A subset of requests to this component are failing — which is exactly what a failed order is evidence of. A deliberate judgement call biased toward halting. |
| `major_outage` | `MAJOR_OUTAGE` | |
| `under_maintenance` | `MAINTENANCE` | |
| absent, `null`, or unrecognised | `UNKNOWN` | Unreadable is not healthy. |

Sources: [Atlassian Statuspage — Status API](https://metastatuspage.com/api),
[Statuspage Manage API](https://developer.statuspage.io/).

### Component object fields that change the parse

Documented fields: `id`, `name`, `status`, `description`, `position`, `group`,
`group_id`, `only_show_if_degraded`, `showcase`, `start_date`, `created_at`,
`updated_at`, `page_id`.

- **`group: true`** marks a container whose status is a roll-up of its children.
  Groups sit in the *same* `components` array as their children, so a single failing
  child appears twice — once as itself, once as its group — unless groups are
  separated out. Alpaca's live feed: 112 components, 11 of them groups. Coinbase's:
  172 components, 7 groups.
- **`group_id`** is the parent's `id` on a child component.
- **`only_show_if_degraded: true`** components are hidden from the rendered page while
  healthy. They still appear in the API payload; do not infer anything from a
  component's absence from the human-readable page.

### `page.updated_at` — do not use it for freshness

The `page` object carries `id`, `name`, `url`, `time_zone` and `updated_at`. That
timestamp records when the page last **changed**, not when it was last confirmed
healthy. Measured on 2026-08-20, Alpaca's `page.updated_at` was ~5.5 hours old and
Coinbase's ~9 hours old, both on pages reading `"indicator": "none"`. Using it as a
staleness bound turns every quiet period into a false alert. Freshness must be measured
from the consumer's own fetch clock.

### Rate limits — the two APIs differ, and the distinction matters

- **Status API** (`/api/v2/*.json`, public, what you poll): Atlassian Support states
  plainly, *"The Manage API is limited to 60 requests per minute. The Status API is not
  rate limited."* The reason to bound your polling is therefore **not** an IP block.
- **Manage API** (`api.statuspage.io/v1`, token-authenticated, used to update a page):
  60 requests/minute per page, and *"Each API token is limited to 1 request / second as
  measured on a 60 second rolling window."* Exceeding it returns `429` (changed from
  `420` on 15 February 2021) with a `Retry-After` header.

Sources: [Atlassian Support — What are the different APIs under
Statuspage?](https://support.atlassian.com/statuspage/docs/what-are-the-different-apis-under-statuspage/),
[Statuspage API documentation](https://developer.statuspage.io/),
[Upcoming rate limiting changes to Statuspage REST
API](https://community.atlassian.com/forums/Statuspage-articles/Upcoming-rate-limiting-changes-to-Statuspage-REST-API/ba-p/1455362).

### Caching — the real reason to bound your poll rate

Response headers observed on both endpoints on 2026-08-20:

```
Cache-Control: max-age=10, public, s-maxage=10, stale-while-revalidate=20, stale-if-error=3600
ETag: W/"..."
Access-Control-Allow-Origin: *
```

Two consequences:

1. **`max-age=10`** — a refetch inside 10 seconds returns the identical cached body.
   Polling faster than the cache TTL buys no freshness at all. A 30–60s interval is
   the useful range.
2. **`stale-if-error=3600`** — when Statuspage's own origin fails, the edge is
   permitted to keep serving the last good body for **up to an hour**. A `200` with
   `"indicator": "none"` during an incident may be a cached artifact rather than a live
   assertion of health. This is the strongest single argument for never treating an
   operational reading as proof and always corroborating with first-party metrics.

Both pages are CDN-fronted (Alpaca via AtlassianEdge/CloudFront, Coinbase via
Cloudflare) and neither returns rate-limit headers.

## 2. Verified broker status feeds

| Broker | Status API URL | Verified | Components / groups | Example dependency components |
|---|---|---|---|---|
| Alpaca | `https://status.alpaca.markets/api/v2/summary.json` | 2026-08-20, HTTP 200, Statuspage v2 schema | 112 / 11 | `Live Trading API`, `Market Data Streaming API`, `Broker API` |
| Coinbase | `https://status.coinbase.com/api/v2/summary.json` | 2026-08-20, HTTP 200, Statuspage v2 schema | 172 / 7 | `Trading` |

Component names are reproduced exactly as published, including capitalisation. They
are the broker's to change; the monitor reports any declared name that matches nothing
as an unmatched dependency, and that report should be alerted on rather than logged and
forgotten.

**Not every broker publishes a Statuspage.** Where a broker publishes status as an HTML
page, an RSS feed or a social account, this parser does not apply — a URL that returns
something without a `status` object yields UNKNOWN, which is correct but useless.
Build a separate ingestion path rather than pointing this one at an arbitrary URL.

## Authentication

Public status pages need none. Private and trial pages require the API key sent as an
`Authorization` header. If you add an authenticated page, keep the key out of the
status-monitor configuration file and load it the way the rest of the deployment loads
secrets — see `centralized-secrets-management-vault-integration`.
