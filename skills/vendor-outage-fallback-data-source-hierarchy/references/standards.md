# Standards — vendor-outage-fallback-data-source-hierarchy

## What is standardised, and what is not

**No regulator, exchange or standards body publishes a market-data staleness limit, a
failover promotion window, or a maximum age for a cached price.** The primary sources
surveyed below require firms to *have, document and test* business continuity
arrangements covering supplier failure; none of them specifies a number. Every threshold
in this skill is a configurable engineering default. Do not present one to a regulator or
a vendor as a compliance figure, and do not treat a failover as a rule breach.

| Parameter | Default | Status |
|---|---|---|
| `max_staleness_seconds` (per node) | 5.0 s | Engineering default. Set it from the feed's *measured* normal inter-tick gap for that instrument and session, not from a vendor SLA. A liquid future and a thinly quoted small-cap do not share a limit. |
| `max_error_threshold` (per node) | 3 | Engineering default. The budget decays by one per good message, so it counts roughly-consecutive failures rather than lifetime failures. |
| `recovery_cooling_seconds` | 30.0 s | Engineering default. The run of unbroken health a challenger must serve before it can take routing from a healthy incumbent. `0.0` disables the hold. |
| `max_synthetic_age_seconds` | 30.0 s | Engineering default, and the single most consequential number here — it is how long you are willing to trade off a price nobody is refreshing. |
| `allow_non_positive_prices` | `False` | Policy. Correct for equities, FX and crypto; wrong for instruments that can legitimately print at or below zero. See "Negative prices" below. |
| `max_event_log_entries` | 10 000 | Engineering default. The ring is telemetry, not the system of record. |
| Priority ranks | 1 / 2 / 3 | Operational choice. Rank by whatever combination of directness, latency, entitlement cost and observed reliability your desk actually cares about. |

## The hierarchy, as a topology

The tiers below are the common shape of a redundant market-data plane. **The latency
figures a vendor will quote you are contractual and situational**; this table therefore
describes what each tier *is* rather than asserting numbers that no primary source
publishes. Measure your own with `market-data-latency-monitoring-per-vendor`.

| Priority | Category | Typical transport | Why it sits here | Failure mode it does not cover |
|---|---|---|---|---|
| 1 | Direct exchange feed (e.g. Nasdaq TotalView-ITCH, CME MDP 3.0) | UDP multicast or direct session | Closest to the matching engine; no intermediary to add its own outage | Loses the whole venue if the venue is down; needs sequence-gap recovery of its own |
| 2 | Enterprise consolidated aggregator (Bloomberg B-PIPE, LSEG Real-Time) | Managed TCP / enterprise distribution | Independent infrastructure and independent entitlement from tier 1 | Not independent of the *exchange*; a venue halt appears on both tiers at once |
| 3 | Cloud or REST feed (e.g. Polygon.io) | Public WebSocket / HTTPS | Different network path, different failure domain, cheap to keep warm | Rate limits and shared-tenancy latency; may be delayed or derived data |
| 4 | Last-known-price cache | In-process memory | Covers the seconds between a tier-3 failure and a human noticing | Nothing is refreshing it. It is a bounded stopgap, never a data source |

Two properties matter more than the ordering:

1. **Independence.** Two vendors that both resell the same upstream fail together. Ask
   each vendor what their tier-1 source is before treating them as separate tiers.
2. **Warmth.** A fallback that is not connected and beating before the incident is not a
   fallback. Cold tiers are discovered at exactly the moment there is no time to fix them.

### Tier-3 vendors are a business risk, not only a technical one

**IEX Cloud was retired on 31 August 2024**, announced on 31 May 2024 — roughly three
months' notice, all endpoints switched off, no successor product, customers left to
migrate themselves — and it still appears in integration guides as a supported
framework. A Priority-3 cloud vendor can disappear entirely, which is a different failure from an
outage and is not solved by adding a Priority 4. Re-verify the tier annually alongside
the RTS 6 Article 14(4) business-continuity test.

Likewise, **"Refinitiv Elektron" is a retired brand**. LSEG completed its acquisition of
Refinitiv in 2021; the real-time products are now LSEG Real-Time and LSEG Real-Time
Optimized. Runbooks that still name Elektron will not match anything in current vendor
documentation.

## Health conditions

A node is usable only if **all** of the following hold. They are evaluated in this order,
cheapest and most certain first:

1. `is_active` is true and the node is not explicitly `DISCONNECTED`. A socket close is
   direct evidence and beats any timer.
2. `error_count < max_error_threshold`.
3. The node has produced **at least one** heartbeat. Registration is a declaration of
   intent, not a measurement.
4. Staleness is within limit, measured on a monotonic clock:

$$\Delta t_{\text{stale}} = t^{\text{mono}}_{\text{now}} - t^{\text{mono}}_{\text{heartbeat}} \;\le\; \text{max\_staleness\_seconds}$$

The comparison is strict (`>` degrades), so a node exactly at its limit is still healthy.

### Why monotonic

Staleness and the promotion window are *durations*. A wall clock is not monotonic: NTP
can step it in either direction, and a leap-second smear moves it continuously.

- Stepped **backwards**, $\Delta t_{\text{stale}}$ goes negative and every staleness
  comparison passes — a frozen feed reads as fresh for the length of the step.
- Stepped **forwards**, every node breaches its limit in the same instant, and a
  correctly functioning system fails over its entire hierarchy onto the synthetic cache.

`last_heartbeat_utc` is retained for the audit trail and is never used for arithmetic.

## Promotion: stability, not elapsed time

$$\text{Promote } s \iff \left(\text{Status}_s = \text{HEALTHY}\right) \;\land\; \left(\text{Status}_{\text{incumbent}} \ne \text{HEALTHY} \;\lor\; t^{\text{mono}}_{\text{now}} - t^{\text{mono}}_{\text{healthy since}(s)} \ge T_{\text{promote}}\right)$$

where $t_{\text{healthy since}(s)}$ is the start of $s$'s current **unbroken** run of
health and is cleared on every unhealthy observation.

Two things this rule deliberately is not:

- **Not "time since the last failover."** After a long outage that clock has expired, so
  a single heartbeat from a still-recovering vendor instantly recaptures routing. The
  first promotion after any long outage — the one most likely to flap — would be the
  only one with no protection at all.
- **Not applied when the incumbent is unhealthy.** Failover away from a failed source is
  immediate and unconditional. Escalate fast, promote slow.

The precedent is networking, not finance: **RFC 2439, "BGP Route Flap Damping"**
(November 1998) suppresses an unstable route and restores it only once its figure of
merit has decayed below the reuse threshold, which requires the route to actually stop
changing. Stability is earned; it is not granted by the passage of time.

## Negative prices

A `price > 0` filter is a good default and a real bug for some instruments. The **WTI
crude oil futures contract for May 2020 settled at −$37.63 per barrel on 20 April 2020**,
the first sub-zero print since the contract began trading in 1983; CME made operational
changes ahead of that week specifically to permit negative prices. Calendar spreads and
some power and gas contracts print at or below zero routinely.

`allow_non_positive_prices` therefore exists and defaults to `False`. Enable it per
deployment for the instruments that need it. `NaN` and `±inf` are rejected in **all**
configurations — those are never a price.

Sources: [U.S. EIA, *Crude oil prices briefly traded below $0 in spring
2020*](https://www.eia.gov/todayinenergy/detail.php?id=46336); [Congressional Research
Service, *Crude Oil Futures Prices Turn Negative*
(IN11354)](https://www.congress.gov/crs_external_products/IN/PDF/IN11354/IN11354.1.pdf).

## Engineering standards for a fallback hierarchy

| Property | Standard | How this skill meets it |
|---|---|---|
| Never overstate freshness | A price handed to a consumer must not claim to be newer than it is | `timestamp` is the observation time; `age_seconds` is populated; `is_synthetic` is set |
| Bounded degradation | The last-resort tier must expire | `max_synthetic_age_seconds`; past it the engine raises instead of returning a price |
| Fail closed | An unmeasured or unevaluable input resolves toward *less* trading, never more | Unbeaten nodes are `DISCONNECTED`; `NaN`/`inf`/malformed quotes are rejected and never cached |
| Monotonic timing | Interval measurement must not depend on a clock that can step | Injected `time.monotonic`; wall-clock stamps are audit-only |
| Escalate fast, promote slow | Failover is immediate; recovery is earned | Unconditional failover; promotion needs an unbroken run of `recovery_cooling_seconds` |
| Anti-flap must not self-harm | A hold must never select a source measured unhealthy | The hold applies only while the incumbent is `HEALTHY` |
| Bounded retries | No unbounded retry against an ambiguous vendor state | Each source is attempted at most once per fetch |
| Deterministic ordering | Two vendors at the same priority must order identically on every restart | Sort key is `(priority, source_id)` |
| Configuration validation | Bad configuration fails at setup, not silently at run time | Duplicate `source_id`, non-positive priority, non-finite or negative thresholds all raise `ValueError` |
| Auditability | Why routing changed must be recoverable afterwards | Typed `FailoverEvent` with a sequence-unique id, tz-aware stamp, previous/new source and reason |
| Thread safety | Feed-handler and strategy threads share this state | One `RLock` guards every public mutating method |

## Regulatory context

This is engineering guidance, not legal advice. The two regimes below apply to
**different populations of firms**, and neither universalises.

### EU / UK — MiFID II RTS 6

Jurisdiction: EU, and the UK as assimilated law. Applies to investment firms engaged in
algorithmic trading — **not** to an individual running a strategy through a retail broker.
Source: [Commission Delegated Regulation (EU) 2017/589 of 19 July
2016](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32017R0589).

| Requirement | Source | Bearing on this skill |
|---|---|---|
| Business continuity arrangements must cover "a range of possible adverse scenarios ... including the unavailability of systems, staff, work space, **external suppliers** or data centres or **loss or alteration of critical data** and documents". | Art. 14(2)(b) | The closest a rule comes to naming what this engine handles. A vendor outage is an in-scope adverse scenario; a priority hierarchy is one documented arrangement for it. The rule mandates *having* arrangements, not any threshold. |
| Arrangements must include "alternative arrangements for the investment firm to manage outstanding orders and positions". | Art. 14(2)(g) | The synthetic-cache tier is *not* this. A bounded cache buys seconds; managing outstanding orders through a data outage is a separate runbook. |
| "An investment firm shall **review and test** its business continuity arrangements on an **annual basis** and modify the arrangements in light of that review." | Art. 14(4) | The concrete, mandatory obligation for in-scope firms, and the reason `assets/checklist.md` puts a dated failover drill at the top. An untested tier 2 is an assumption. |
| A firm "shall ensure that its trading algorithm or trading system can be shut down ... **without creating disorderly trading conditions**". | Art. 14(3) | Constrains what you do *after* this engine reports a total outage. Flattening at market on prices the engine has just declared unusable is a plausible way to create exactly those conditions. |
| A firm "shall remain fully responsible for its obligations under this Regulation where it outsources or procures software or hardware used in algorithmic trading activities." | Art. 4(1) | Vendor failure does not transfer responsibility. Note the article names software and hardware; the data-supplier scenario is anchored in Art. 14(2)(b) above, not here. |

### US — SEC Rule 2a-5 (fair value determination)

Jurisdiction: US. Applies to **registered investment companies and their valuation
designees** — not to a proprietary firm's feed handler and not to an individual trader.
Source: [17 CFR § 270.2a-5](https://www.law.cornell.edu/cfr/text/17/270.2a-5).

- § 270.2a-5(a)(4) requires "[o]verseeing pricing service providers, if used, including
  establishing the process for approving, monitoring, and evaluating each pricing service
  provider and initiating price challenges as appropriate."
- § 270.2a-5(c) provides that a market quotation "is readily available only when that
  quotation is a quoted price (unadjusted) in active markets for identical investments
  that the fund can access at the measurement date, provided that a quotation will **not**
  be readily available if it is **not reliable**."

The second limb is the useful principle even outside the rule's scope: a quotation that
is not reliable is not a usable price. That is precisely what `is_synthetic=True` plus a
non-zero `age_seconds` is telling the consumer, and why `max_synthetic_age_seconds`
refuses rather than degrades.

### What does *not* apply

**Regulation SCI does not apply to a trading firm running a feed handler.** Under 17 CFR
§ 242.1000, "SCI entity" means "an SCI self-regulatory organization, SCI alternative
trading system, plan processor, exempt clearing agency subject to ARP, or SCI competing
consolidator". Reg SCI's systems-resilience and business-continuity obligations are
frequently cited at market-data infrastructure in general; they bind exchanges, SIPs,
certain ATSs and competing consolidators, not their customers. Source:
[17 CFR § 242.1000](https://www.law.cornell.edu/cfr/text/17/242.1000).
