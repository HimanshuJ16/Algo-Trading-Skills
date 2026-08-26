---
name: market-data-entitlement-and-licensing-per-venue
description: >-
  Fail-closed pre-stream gate for exchange market data entitlements — per-venue
  licences, depth-of-book tier, non-display activity category, and professional
  vs non-professional subscriber classification — with an auditable reason
  recorded for every decision.
domain: Data Management Global
subdomain: Exchange Licensing & Data Entitlement Governance
tags: ["market-data", "entitlements", "exchange-licensing", "non-display-license", "professional-subscriber", "cta-utp-plan", "cme-non-display", "depth-of-book"]
brokers_frameworks: ["Nasdaq US Equities and Options Data Policies", "CTA/CQ Nonprofessional Subscriber Policy", "CME Group Information License Agreement", "London Stock Exchange Non-Display Declaration", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a system inside the firm — a strategy, a risk engine, a
research notebook, a client-facing screen — is about to open a market data
stream from a trading venue, and someone must be able to prove afterwards that
the firm had licensed *that venue*, at *that depth*, for *that kind of use*, by a
subscriber classified the way the venue's rules require.

The gate exists because market data breaches are discovered late and priced
retroactively. The CTA Nonprofessional Subscriber Policy states it plainly: "If
NYSE finds that the vendor has incorrectly qualified a professional subscriber as
nonprofessional, the vendor will be liable for retroactive fees billed by NYSE
for the subscriber at the professional rate." A wrong approval today is a
back-fee assessment, with interest, months or years from now.

The single most expensive modelling mistake in this area is a firm-wide
"we have a non-display licence" flag. No major venue licenses non-display that
way — see **Prerequisites** and `references/standards.md`.

## When NOT to Use

- **Not a fee calculator or a usage declaration.** It decides access; it does not
  produce reportable units. Nasdaq's non-display unit of count is the greater of
  (a) the number of Subscribers that can modify the application in real time or
  (b) the number of Devices (usually servers) that receive and benefit from the
  Information. Derive that from your infrastructure inventory, never from stream
  requests.
- **Not a replacement for the vendor's permissioning system.** LSEG DACS and
  Bloomberg EMRS enforce entitlements at the feed itself. This gate sits upstream
  and does not remove the need to configure them.
- **Not for vendor contract scope** (licensed use cases, redistribution rights,
  seat caps) — that is `data-vendor-contractual-usage-restriction-tracking`.
- **Not for real-time vs delayed tiering** or for blocking execution on delayed
  quotes — that is `real-time-vs-delayed-data-entitlement-handling`.
- **Not a legal opinion.** It enforces the scope a compliance owner has encoded
  from the executed Order Forms and agreements. Reading them is still a human job.

## Prerequisites

- `VenueEntitlement` — one per licensed venue: `venue_id`, `max_data_level`
  (`L1`/`L2`/`L3`, deepest licensed tier), `non_display_categories` (subset of
  `PRINCIPAL`, `CLIENT_FACILITATION`, `TRADING_PLATFORM`; empty means
  display-only), `license_expiry_date` (ISO-8601 `YYYY-MM-DD`, or `None` for
  "not tracked here").
  **Venues licensed separately need separate records.** CME Group charges
  non-display per Designated Contract Market, so `CME`, `CBOT`, `NYMEX` and
  `COMEX` are four entitlements, not one. LSE declares per segment and per level.
- `UserEntitlementProfile` — `user_id`, `account_holder_type`
  (`NATURAL_PERSON`/`ORGANISATION`), `declared_classification`
  (`PROFESSIONAL`/`NON_PROFESSIONAL`), `is_securities_professional`,
  `classification_attested_on` (ISO-8601; required for a `NON_PROFESSIONAL`
  declaration), `venue_entitlements`.
- `DataStreamRequest` — `user_id`, `venue_id`, `data_level`, `usage_type`
  (`DISPLAY`/`NON_DISPLAY_ALGO`), `non_display_category` (required when
  `usage_type` is `NON_DISPLAY_ALGO`).
- A durable store for the returned `EntitlementAuditReport` objects. The engine
  keeps no record of its own.

## Workflow

Checks run in this order and short-circuit on the first denial. The order is part
of the contract: it determines which `status` an auditor sees for a request that
breaches more than one rule.

1. **Subscriber identity** — if `request.user_id` does not equal
   `profile.user_id`, deny with `ENTITLEMENT_DENIED_SUBSCRIBER_MISMATCH`.
   Entitlements are not transferable; evaluating one subscriber's request against
   another's profile silently lends out licences and files the decision under the
   wrong name.
2. **Usage type recognised** — if `usage_type` is neither `DISPLAY` nor
   `NON_DISPLAY_ALGO`, deny with `ENTITLEMENT_DENIED_UNRECOGNISED_USAGE_TYPE`.
   A near-miss such as `NON_DISPLAY` must never fall through to the display path
   and skip the non-display gate entirely.
3. **Classification integrity** — deny with
   `ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER` when a `NON_PROFESSIONAL`
   declaration cannot stand: the account holder is an `ORGANISATION` (only
   natural persons can qualify), the subscriber is a Securities Professional, or
   the request is automated non-display consumption. `PROFESSIONAL` is never a
   defect — it is the default classification, and over-declaring it costs money
   rather than creating audit exposure.
4. **Classification freshness** — a `NON_PROFESSIONAL` declaration with no
   `classification_attested_on`, a future-dated one, or one older than
   `max_attestation_age_days` (default 183, the semi-annual CTA re-verification
   cadence) is denied with `ENTITLEMENT_DENIED_STALE_CLASSIFICATION`.
5. **Venue licensed** — if the normalised `venue_id` has no `VenueEntitlement`,
   deny with `ENTITLEMENT_DENIED_UNLICENSED_VENUE`. Venue ids are normalised on
   *both* sides; a duplicate entitlement for one venue is a configuration error,
   not a silent last-one-wins.
6. **Licence term** — if `as_of_date` is past `license_expiry_date`, deny with
   `ENTITLEMENT_DENIED_EXPIRED_LICENSE`. The expiry date itself is the last
   licensed day. An entitlement with `license_expiry_date=None` is **not** gated
   on expiry; the engine logs a warning once per subscriber/venue so the omission
   is visible rather than silent.
7. **Depth licensed** — if the requested `data_level` is deeper than
   `max_data_level`, deny with `ENTITLEMENT_DENIED_UNLICENSED_DATA_LEVEL`. An
   `L2` entitlement covers `L1`; it does not cover `L3`. Depth-of-book is a
   separately licensed product everywhere this skill applies.
8. **Non-display activity licensed** — for `NON_DISPLAY_ALGO`, deny with
   `ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE` when the venue entitlement is
   display-only, when the request names no `non_display_category`, or when the
   named category is not licensed at that venue. Trading as principal and
   facilitating client business are separate licences (CME Category A1 vs A2; LSE
   Principal vs Client Facilitation), so the engine refuses to guess.
9. **Audit report** — return an `EntitlementAuditReport` carrying the decision,
   the reason, and the normalised inputs it was made against. Persist it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **One firm-wide non-display flag.** A boolean licensed by the CME desk will
  happily authorise a Nasdaq depth feed it has no rights to. Non-display fees and
  reporting "vary depending upon the Nasdaq data product", are charged per DCM at
  CME Group, and are declared per segment and level at LSE. Model the entitlement
  as (venue, depth, activity category) or the gate approves breaches.
- **Treating a display entitlement as covering the algo.** Nasdaq Basic is
  display-only — "Non-Display Usage is NOT included". Non-display is fee-liable
  regardless of whether the OMS/EMS runs in the cloud, in a datacenter, or on a
  desktop, so pointing a strategy at a desktop-tier feed does not make it
  display use.
- **Falling through on an unrecognised usage type.** A gate that compares
  `usage_type` to one literal and does nothing in the `else` will approve
  `NON_DISPLAY`, `NONDISPLAY_ALGO` and `algo` as if they were display use. An
  unclassified usage cannot be shown to be licensed; refuse it.
- **Skipping expiry when no expiry is recorded.** "No expiry on file" is not
  "licence valid forever". Either populate the date from the Order Form or accept
  — visibly, via the logged warning — that the term is unenforced for that venue.
- **Placeholder expiry dates.** A hard-coded far-future date authorises
  everything until the day it passes, then denies every venue at once, mid-session.
- **Declaring a bot as Non-Professional to cut the monthly bill.** Only natural
  persons can qualify, and an account held in an organisation's name is
  Professional even when one human uses it personally. The correction arrives as
  retroactive professional-rate fees on the distributor.
- **Attesting Non-Professional status once and never again.** Retired and
  inactive professionals must re-verify semi-annually under CTA policy; a
  never-refreshed attestation quietly becomes false.
- **Assuming CME Group is one venue.** Automated trading using additional DCMs
  requires additional Category A licensing. `CME` in your config is not `NYMEX`.

## Verification

- Instantiate `MarketDataEntitlementEngine`. Build a `PROFESSIONAL`,
  `ORGANISATION` profile holding two entitlements: `CME` at `L2` with
  `non_display_categories=("PRINCIPAL",)` expiring `2026-12-31`, and `NASDAQ` at
  `L1` with no non-display categories.
- Request `CME` / `L2` / `NON_DISPLAY_ALGO` / `PRINCIPAL` at
  `as_of_date=date(2026, 6, 15)` $\implies$ `ENTITLEMENT_APPROVED`.
- Request `NASDAQ` / `L1` / `NON_DISPLAY_ALGO` $\implies$
  `ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE` — the CME non-display licence
  does not travel.
- Request `CME` / `L3` $\implies$ `ENTITLEMENT_DENIED_UNLICENSED_DATA_LEVEL`.
- Request `CME` with `non_display_category="CLIENT_FACILITATION"` $\implies$
  `ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE`.
- Request `usage_type="NON_DISPLAY"` $\implies$
  `ENTITLEMENT_DENIED_UNRECOGNISED_USAGE_TYPE`, not an approval.
- Request under a different `user_id` than the profile's $\implies$
  `ENTITLEMENT_DENIED_SUBSCRIBER_MISMATCH`.
- Evaluate with `as_of_date` one day past the expiry $\implies$
  `ENTITLEMENT_DENIED_EXPIRED_LICENSE`; on the expiry date itself $\implies$
  approved.
- Run `python -m unittest discover -s skills/market-data-entitlement-and-licensing-per-venue/scripts`.

## Related Skills

- `real-time-vs-delayed-data-entitlement-handling`
- `data-vendor-contractual-usage-restriction-tracking`
- `market-data-cost-optimization-tiered-subscriptions`
- `order-book-depth-processing-l2-l3`
