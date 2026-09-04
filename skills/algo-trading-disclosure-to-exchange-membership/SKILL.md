---
name: algo-trading-disclosure-to-exchange-membership
description: >-
  Use when an order is generated or parameterised by an algorithm and the venue or
  regulator requires algorithm identification, registration or disclosure; blocks orders
  whose exchange-facing metadata is missing or stale, before FIX serialisation.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: compliance, regulatory, mifid-ii, sebi, algo-id, pre-trade-risk
  brokers_frameworks: generic-fix
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## Purpose and Scope

Use this skill to implement the compliance gate between order generation and the
venue adapter. It validates the metadata that will actually leave the trading
system; it does not determine legal classification or replace a jurisdictional
legal review.

The control supports a registry containing simple statuses or richer
venue-scoped, version-scoped `AlgoRegistration` records. It returns a
structured `OrderComplianceReport` with a stable `reason_code` for audit and
alert routing.

## When to Use

Use this skill when an order may be generated or materially parameterized by an
algorithm and the venue, broker, or regulator requires algorithm identification,
registration, or disclosure controls. Apply it before FIX serialization and
again at the child-order boundary if a smart order router can create new
outbound orders.

Regulatory requirements are not uniform:

- In the EU, MiFID II and RTS 6 require firms and venues to identify and control
  algorithmic trading activity, while the exact order flag, algorithm
  identifier, and transaction-reporting fields depend on the venue and reporting
  path.
- In India, SEBI and exchange operating procedures can require a unique
  exchange-issued identifier on algo orders. Configure the current exchange
  rule, not an EU assumption, in the registry and venue adapter.

## When NOT to Use

Do not use this skill as the sole control for position limits, price collars,
message-rate limits, market-abuse surveillance, transaction reporting, or FIX
session controls. Combine it with the dedicated risk and reporting skills.

Do not infer that every EU automated order needs one universal pre-registered
identifier. A pure routing system may fall outside the MiFID II definition of
algorithmic trading, and venue-specific tagging rules still require confirmation.
Manual orders should use the separate trader or short-code control path.

## Prerequisites

- Confirmed jurisdiction, venue, broker, order-routing path, and current venue
  tagging specification.
- A version-controlled registry with approval status, effective ownership, and
  optional approved venues and algorithm version.
- A stable upstream classification for `is_algorithmic` with documented legal
  ownership; do not derive it from the presence of `algo_id`.
- A pre-FIX interception point and a venue adapter test that proves the approved
  identifier survives serialization into the actual outbound tag.
- Durable compliance logs and an alert route for hard rejects, registry outages,
  and attempted stale deployments.
- Python 3.10+ for the reference implementation.

## Inputs and Outputs

`OutboundOrder` requires `order_id`, `symbol`, and an explicit
`is_algorithmic` value. It can carry `algo_id`, `trader_id`, `venue`,
`algo_version`, and `parent_algo_id`.

`AlgoRegistration` supports `status`, optional `venues`, and optional `version`.
A plain registry value such as `{"VWAP_V2.0": "APPROVED"}` remains supported.
`venues` must be a collection of venue codes; a bare string is rejected because
it would otherwise iterate per character and widen the approved scope.

The engine validates the whole registry at construction and raises on a
malformed, blank, or duplicate entry, so a bad registry fails at deployment
rather than at order time. The validated snapshot is exposed read-only, and
`evaluate_order` returns an `OrderComplianceReport` without sending or mutating
an order.

Matching rules are deliberately asymmetric: venue codes are compared
case-insensitively, while `algo_id` and `algo_version` must match the registry
exactly. Both directions fail closed.

## Workflow

1. **Resolve the applicable rule**: identify the venue and jurisdiction and
   document whether the order requires algorithm disclosure, a venue-issued
   identifier, a transaction-reporting identifier, or more than one field.
2. **Classify the order upstream**: set `is_algorithmic` from the approved
   classification service. Do not treat a missing `algo_id` as evidence that an
   order is manual.
3. **Validate the order envelope**: reject blank identifiers, invalid field
   types, manual orders without `trader_id`, and manual orders that carry any
   algorithmic metadata — `algo_id`, `parent_algo_id`, or `algo_version`. A
   manual order carrying algorithm lineage is a classification defect upstream,
   not a field to ignore.
4. **Validate algorithm disclosure**: for an algorithmic order, require the
   exchange-facing `algo_id`, look it up in the immutable registry, and require
   status `APPROVED`.
5. **Apply scope checks**: if the registration is venue- or version-scoped,
   require an exact match. If a child order supplies `parent_algo_id`, require
   it to match the child `algo_id`.
6. **Gate and record**: allow only compliant orders to reach the venue adapter.
   Hard-reject all other orders, persist the reason code and registry status,
   and alert on repeated or unknown identifiers.
7. **Verify the wire payload**: in integration tests and deployment checks,
   inspect the serialized FIX or broker payload. The in-memory field alone does
   not prove that the venue received the required tag.

## Failure Modes and Recovery

- **Unknown, pending, deprecated, or suspended ID**: hard-reject, alert, and
  restore the last approved registry or deployment; never auto-approve.
- **Registry unavailable or malformed**: fail closed, preserve the order intent
  for operator review, and recover by restoring a signed, versioned snapshot.
- **Venue or version mismatch**: stop the affected route, verify the approval
  record and deployment manifest, then redeploy only after the registry and
  venue adapter agree.
- **Child tag missing or changed**: cancel or prevent further child orders,
  reconcile already-submitted orders, and fix propagation before reopening flow.
- **Unexpected reject-rate spike**: activate the operational kill switch for the
  affected strategy or venue, inspect reason-code metrics, and roll back the
  most recent registry or adapter change.

## Common Pitfalls

- Treating MiFID II, SEBI, and individual exchange procedures as one universal
  `algo_id` rule.
- Hard-coding a FIX tag number without validating the current venue or broker
  specification and the raw wire message.
- Registering a strategy once, then deploying a materially changed version
  without an approval transition and deployment record.
- Tagging a parent order while a smart order router emits untagged or differently
  tagged child orders.
- Declaring a venue scope as a bare string (`venues="XNYS"`) instead of a
  collection. A string iterates per character, so the scope silently becomes
  `{"X", "N", "Y", "S"}` — rejecting the intended venue while approving an
  unrelated one named `X`.
- Merging registry sources without checking for keys that collapse to the same
  identifier once whitespace is trimmed; last-wins can silently promote a
  `SUSPENDED` algorithm to `APPROVED`.
- Mutating the engine's registry in place to hot-fix an approval, bypassing the
  controlled deployment process and the audit record it produces.
- Treating a missing registry entry as a temporary warning instead of a hard
  pre-trade failure.
- Logging only free-text messages and losing the stable reason code needed for
  monitoring, audit, and incident recovery.

## Verification

Run:

```text
python -m unittest discover -s skills/algo-trading-disclosure-to-exchange-membership/scripts
```

The tests cover approved, unknown, pending, deprecated, manual, malformed,
venue-scoped, version-scoped, parent/child, and invalid-input paths, plus the
registry-construction failures (bare-string venue scope, duplicate keys, blank
registered version, read-only snapshot). Add a
venue-adapter integration test that asserts the exact outbound tag and a
non-production deployment test that proves rollback restores the prior approved
registry.

## Related Skills

- `india-sebi-algo-trading-tagging-requirements`
- `mifid-ii-algo-trading-compliance-eu`
- `uk-fca-algorithmic-trading-systems-controls`
- `paper-to-live-promotion-checklist`
- `kill-switch-and-drawdown-circuit-breakers`
