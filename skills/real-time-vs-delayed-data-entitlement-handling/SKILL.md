---
name: real-time-vs-delayed-data-entitlement-handling
description: >-
  Use at the point a consumer asks for data and something must decide between a live
  stream, a delayed stream carrying its delay label, or a refusal, using each venue's
  own delay definition. Depth and non-display licensing is separate.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: entitlement-handling, real-time-data, delayed-data, delay-interval-policy, display-requirements, exchange-licensing, market-data-compliance
  brokers_frameworks: "Nasdaq Display Requirements Policy; CME Group Data Licensing Policy Guidelines; ICE Futures Europe Market Data Policy; MiFIR Article 13 / ESMA market data terminology; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill at the point where a system asks for market data on an instrument
and something must decide *which tier it gets*: a live stream, a delayed stream
carrying a delay label, or a refusal. It exists for two failures that are cheap to
prevent and expensive to discover:

1. **A strategy priced off stale quotes.** A delayed entitlement is the usual sign
   that the real-time licence for that venue was never bought. An execution path
   fed by a ten- or fifteen-minute-old book is trading against a market it cannot
   see.
2. **A delay interval invented by the application instead of read off the venue's
   policy.** "Delayed means 15 minutes" is a Nasdaq/ESMA convention, not a
   universal one. CME Group defines Real Time Information as "made available
   within ten (10) minutes of initial transmission" and Delayed Information as
   "more than ten (10) minutes, but less than eight (8) hours" old; ICE Futures
   Europe likewise "defines real-time data as any market data that is < 10
   minutes old." A feed throttled by eight minutes and served as "delayed" is
   real-time Information at those venues, at real-time rates.

## When NOT to Use

- **Not a per-venue licence gate.** Depth of book, non-display activity category,
  and whether a Non-Professional declaration can stand belong to
  `market-data-entitlement-and-licensing-per-venue`. This skill validates the
  subscriber classification and records it; it does not adjudicate it.
- **Not a latency or staleness monitor.** A `REAL_TIME` entitlement is a licensing
  fact, not a measurement. It says nothing about whether the feed is currently
  keeping up — see `market-data-latency-monitoring-per-vendor` and
  `model-staleness-detection`.
- **Not for strategies that legitimately trade on delayed data.** Some venues
  license automated use of delayed Information — CME requires Non-Display Use "of
  Real Time and Delayed Information" to be reported per Application, which
  presupposes the use exists. If a slow strategy runs on delayed prices under a
  licence that permits it, this gate's unconditional execution block is the wrong
  control.
- **Not a fee calculator or a usage declaration.** It counts nothing and reports
  nothing to a venue.
- **Not a substitute for the vendor's permissioning system.** LSEG DACS and
  Bloomberg EMRS enforce entitlements at the feed itself; this gate sits upstream.

## Prerequisites

- `UserEntitlement` — `user_id`, `subscriber_type` (`PROFESSIONAL` /
  `NON_PROFESSIONAL`), `subscribed_exchanges` (any iterable of venue ids, matched
  case-insensitively; a bare string is rejected, not iterated character by
  character), `entitlement_tier` (`REAL_TIME` / `DELAYED` — anything else is
  denied).
- `MarketDataRequest` — `symbol`, `exchange`, `is_trading_execution_request`.
- One `VenueDelayPolicy` per venue whose **delayed** feed may be served, populated
  from that venue's published data policy:
  - `delay_minutes` — the delay the firm actually applies.
  - `min_delay_minutes` — the smallest delay that counts as delayed *at that
    venue*. Nasdaq/ESMA: 15. CME Group and ICE Futures Europe draw the line above
    ten minutes, so: 11.
  - `max_delay_minutes` — optional ceiling beyond which the feed is end-of-day or
    historical Information under a separate licence. CME caps delayed Information
    below eight hours: 479.
  - `display_label` — the Prominent Delay Message, verbatim. Nasdaq's own
    examples: `Data Delayed 15 minutes`, `Del-15`, `Data Delayed 24 hours`.
  - `delay_message_refresh_seconds` — default 90, the cadence Nasdaq requires on a
    scrolling ticker.
  - `policy_source` — citation of the document and version the numbers came from.
- A durable store for the returned `EntitlementAuditReport` objects. The engine
  keeps no record of its own.

## Workflow

Checks run in this order and short-circuit on the first denial. The order is part
of the contract: it decides which `status` an auditor sees when a request breaches
more than one rule.

1. **Structural validation** — blank ids, an unknown `subscriber_type`, a
   non-boolean execution flag, or a bare-string venue list raise
   `EntitlementConfigurationError`. Malformed input is a defect, not a compliance
   decision; evaluating it anyway yields an authoritative-looking approval backed
   by nothing.
2. **Tier recognised** — a tier outside `{REAL_TIME, DELAYED}` is denied with
   `ENTITLEMENT_DENIED_UNRECOGNISED_TIER`. It is checked first because an
   unclassified tier makes every later question unanswerable. A gate that only
   compares the tier to `DELAYED` and serves everything else lets `REALTIME`,
   `Real Time` and `""` skip the execution block entirely.
3. **Venue subscribed** — an exchange absent from `subscribed_exchanges` is denied
   with `EXCHANGE_NOT_SUBSCRIBED`. Ids are normalised on both sides.
4. **Execution on a delayed feed** — `is_trading_execution_request` on a `DELAYED`
   tier is refused with `LIVE_TRADING_BLOCKED_DELAYED_DATA`, *before* any stream
   is shaped, so the caller never receives a permitted-looking report it can act
   on.
5. **Real-time serve** — `REALTIME_STREAM_ENTITLED`, delay 0, execution allowed,
   no display label. Needs no venue delay policy.
6. **Delayed serve** — only against an explicit `VenueDelayPolicy`:
   - no policy for the venue $\implies$ `DELAYED_STREAM_BLOCKED_NO_DELAY_POLICY`.
     The engine will not assume fifteen minutes for a venue it was told nothing
     about.
   - `delay_minutes < min_delay_minutes` $\implies$
     `DELAYED_STREAM_BLOCKED_INSUFFICIENT_DELAY`. Under-throttled data is
     real-time Information.
   - `delay_minutes > max_delay_minutes` (when tracked) $\implies$
     `DELAYED_STREAM_BLOCKED_DELAY_EXCEEDS_POLICY` — that is a different licensed
     product.
   - otherwise `DELAYED_STREAM_ENTITLED`, carrying `delay_minutes`,
     `required_display_label` and `delay_message_refresh_seconds`. Rendering that
     label is the caller's obligation; the engine cannot draw the screen.
7. **Audit report** — persist every `EntitlementAuditReport`, denials included.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hard-coding a 15-minute delay.** Applied to CME or ICE, a delay chosen by the
  application rather than the venue is either over-generous (paying real-time
  rates for a product you throttled anyway) or a breach (serving Real Time
  Information under a delayed licence). Read the number off the venue's policy and
  put it in the `VenueDelayPolicy`.
- **Treating "not DELAYED" as "real-time".** Any gate whose delayed branch is an
  equality test on one literal will serve `REALTIME`, `RT`, `Real Time` and `""`
  down the permitted path and skip the execution block. Deny the unrecognised tier
  instead.
- **Asserting a delay on a denial.** A report for a refused request that still
  says `is_delayed=True, delay_minutes=15` describes a stream nobody received; an
  auditor reads it as data served. On a denial this engine sets `delay_minutes`
  to `None`.
- **Displaying delayed prices with no delay message.** Nasdaq requires a Prominent
  Delay Message on all displays of Delayed Data, "at or near the top of the page",
  and on a ticker "interspersed with the market data at least every 90 seconds".
  The engine hands you the label; a screen that drops it breaches on the day it
  ships.
- **Assuming delayed means free and unlicensed.** Nasdaq says only that "there may
  not be a charge for the usage of the delayed data, *depending upon the product
  selected*". CME requires Non-Display Use of Real Time **and Delayed**
  Information to be reported per Application. The free-15-minute obligation in
  MiFIR Article 13(1) binds EU trading venues — it is not a global rule and it is
  not a licence to consume delayed data any way you like.
- **Reading `REAL_TIME` as "the data is fresh".** It is a licensing tier. A
  real-time entitlement served by a lagging or gapped feed is still stale data;
  monitor freshness separately.
- **Falling through on an unrecognised `status`.** Callers routing on `status`
  must treat anything outside `ALL_STATUSES` as a denial.

## Verification

- Instantiate `RealTimeVsDelayedEntitlementEngine` with a Nasdaq policy
  (`delay_minutes=15`, `min_delay_minutes=15`,
  `display_label="Data Delayed 15 minutes"`) and a CME policy
  (`delay_minutes=11`, `min_delay_minutes=11`, `max_delay_minutes=479`).
- `DELAYED` + NASDAQ + execution request $\implies$
  `LIVE_TRADING_BLOCKED_DELAYED_DATA`, `is_permitted=False`,
  `delay_minutes is None`.
- `REAL_TIME` + NASDAQ $\implies$ `REALTIME_STREAM_ENTITLED`, `delay_minutes == 0`,
  `trading_execution_allowed=True`, no display label.
- `DELAYED` + CME $\implies$ `DELAYED_STREAM_ENTITLED` with `delay_minutes == 11`
  — the venue's number, not fifteen.
- `DELAYED` + a venue with no policy $\implies$
  `DELAYED_STREAM_BLOCKED_NO_DELAY_POLICY`.
- A CME policy with `delay_minutes=10` $\implies$
  `DELAYED_STREAM_BLOCKED_INSUFFICIENT_DELAY`; at 11 it is served.
- `entitlement_tier="REALTIME"` with an execution request $\implies$
  `ENTITLEMENT_DENIED_UNRECOGNISED_TIER`, never a permitted stream.
- `subscribed_exchanges="NASDAQ"` (a bare string) $\implies$
  `EntitlementConfigurationError`.
- Run `python -m unittest discover -s skills/real-time-vs-delayed-data-entitlement-handling/scripts`.

## Related Skills

- `market-data-entitlement-and-licensing-per-venue`
- `market-data-cost-optimization-tiered-subscriptions`
- `market-data-latency-monitoring-per-vendor`
- `data-vendor-contractual-usage-restriction-tracking`
