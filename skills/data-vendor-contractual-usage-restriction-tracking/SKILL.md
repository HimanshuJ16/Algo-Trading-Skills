---
name: data-vendor-contractual-usage-restriction-tracking
description: Fail-closed pre-access gate enforcing market data vendor contract scope
  — licensed use cases, non-display entitlement, external redistribution, seat caps,
  and contract term — with an auditable reason recorded for every decision.
domain: Data Management Global
subdomain: Vendor Data Governance
tags:
- vendor-compliance
- data-licensing
- non-display-trading
- bloomberg-bpipe
- lseg-dacs
- redistribution-audit
- entitlement-tracking
brokers_frameworks:
- LSEG DACS (formerly Refinitiv DACS)
- Bloomberg EMRS
- Nasdaq Global Data Agreement
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when an internal system — a research notebook, a risk engine, an
execution algo, a client-facing portal — asks for vendor market data, and the firm
must decide whether that specific purpose is inside the scope it actually licensed
(Bloomberg B-PIPE, LSEG Real-Time / DACS, ICE Data Services, S&P Capital IQ).

The gate exists because licensing breaches are discovered late and priced
retroactively. Under the Nasdaq Global Data Agreement, any use of the Information
not already provided for in the Nasdaq Requirements — expressly including derived
information, retransmission, redistribution and index calculation — requires prior
written approval and payment of the applicable fees (GDA s.4(c)). Nasdaq may audit
a Distributor's records, reports and systems, normally no more than once in twelve
months (s.7(a)). Where a Final Audit finds underreporting, the amounts plus
interest are due within sixty days, and for a good-faith error the Distributor's
liability reaches back **three years** (s.7(e)); underreporting of 10% or more of
reported Reportable Units also makes the Distributor liable for Nasdaq's audit,
legal and administrative costs (s.7(f)). Other venues and vendors impose their own
audit clauses on comparable terms — read yours.

## When NOT to Use

- **Not a fee calculator.** It counts contractual seats, not fee-liable units. Its
  seat counter cannot produce a vendor invoice or an exchange usage declaration —
  see the unit-of-count pitfall below.
- **Not a replacement for the vendor's entitlement system.** Bloomberg EMRS and
  LSEG DACS enforce permissioning at the feed itself. This gate sits upstream of
  them and does not remove the need to configure them correctly.
- **Not for venue-level entitlement or subscriber classification.** Professional
  vs non-professional status, per-venue licences and real-time vs delayed tiers
  belong to `market-data-entitlement-and-licensing-per-venue` and
  `real-time-vs-delayed-data-entitlement-handling`.
- **Not a legal opinion.** It enforces the scope a compliance owner has encoded.
  Reading the contract and encoding it correctly is still a human job.

## Prerequisites

- `VendorContractSpec`: `vendor_id`, `vendor_name`, `license_tier`,
  `allowed_use_cases`, `is_non_display_allowed`, `is_redistribution_allowed`,
  `max_concurrent_entitlements`, `current_active_entitlements` (default 0),
  `contract_expiration_date` (ISO-8601 `YYYY-MM-DD`, or `None` for "not tracked").
- `DataAccessRequest`: `request_id`, `vendor_id`, `requested_by_system`,
  `use_case_type`, `is_external_redistribution`, `requested_seats` (default 1).
- A durable store for the returned `VendorUsageAuditReport` objects. The engine's
  in-memory buffer is bounded and is not the retained audit record.

## Workflow

Checks run in this order and short-circuit on the first denial. The order is part
of the contract: it determines which `status` an auditor sees for a request that
breaches more than one restriction.

1. **Contract term gate** — if `as_of_date` is past `contract_expiration_date`,
   deny with `CONTRACT_EXPIRED`. Checked first because a lapsed term withdraws
   every other permission. A contract registered with `contract_expiration_date=None`
   is **not** gated on expiry; the engine logs a warning once per vendor so the
   omission is visible rather than silent.
2. **External redistribution gate** — deny with `REDISTRIBUTION_LICENSING_VIOLATION`
   when the request is external redistribution and the contract does not permit it.
   A request counts as redistribution if **either** `is_external_redistribution` is
   True **or** `use_case_type == "EXTERNAL_REDISTRIBUTION"`; the two signals can
   disagree, and this is the breach with the longest back-fee tail, so it fails
   closed on either one alone.
3. **Non-display gate** — if `use_case_type == "NON_DISPLAY_TRADING"` and
   `is_non_display_allowed` is False, deny with `NON_DISPLAY_LICENSING_VIOLATION`.
   Non-display means machine access without a natural person reading a display; it
   is fee-liable whether the engine runs on a desktop, in a datacenter or in the
   cloud.
4. **Licensed scope gate** — if the normalised `use_case_type` is not in
   `allowed_use_cases`, deny with `UNAUTHORIZED_USE_CASE_VIOLATION`. Unlisted means
   unlicensed: never widen the list to make a request pass.
5. **Concurrency headroom gate** — if `current_active_entitlements + requested_seats`
   would exceed `max_concurrent_entitlements`, deny with `CONCURRENCY_CAP_EXCEEDED`.
   A request landing exactly on the cap is approved.
6. **Reserve and record** — on approval the seats are reserved against the contract
   and a `VendorUsageAuditReport` is returned. Denials never mutate contract state.
   When the consuming system disconnects, call `release_entitlement(vendor_id, seats)`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Approving without ever releasing.** Every approval reserves seats permanently
  until `release_entitlement` returns them. A gate wired into connection setup but
  not into teardown drifts into denying compliant requests within hours, and the
  usual "fix" is to raise the cap above what the contract actually licensed.
- **Reading the seat counter as a reportable unit count.** Nasdaq's non-display
  unit of count is the *greater* of (a) the number of Subscribers that can modify
  the application in real time or (b) the number of Devices (usually servers) that
  receive and benefit from the Information. A per-request seat counter will
  understate it. Derive declarations from your infrastructure inventory.
- **Assuming "derived" means "unrestricted".** Derived Data is information that
  cannot be reverse-engineered back into Exchange Information or into a recognisable
  substitute for it. A client-facing chart of vendor quotes, a redistributed index
  recalculated from them, or a "summary" from which the underlying prices can be
  recovered is not derived data — it is redistribution, and GDA s.4(c) requires
  prior written approval and fees before it happens.
- **Trusting one of two disagreeing signals.** A caller that sets
  `use_case_type="EXTERNAL_REDISTRIBUTION"` but leaves `is_external_redistribution`
  False is still redistributing. Gate on both.
- **Placeholder expiry dates.** A hard-coded future `contract_expiration_date`
  authorises everything until the day it passes and then denies everything at once.
  Populate it from the executed contract or leave it `None` and track expiry
  elsewhere.
- **Running desktop-terminal data through an algo.** A research desktop
  subscription does not carry non-display rights; pointing an automated strategy at
  it is a licensing breach regardless of how the bytes reached the process.

## Verification

- Instantiate `VendorUsageRestrictionEngine`. Register a B-PIPE-style contract
  (`is_non_display_allowed=True`, `is_redistribution_allowed=False`,
  `max_concurrent_entitlements=10`, `current_active_entitlements=2`,
  `contract_expiration_date="2027-12-31"`).
- Submit an internal HFT request (`use_case_type="NON_DISPLAY_TRADING"`,
  `is_external_redistribution=False`) $\implies$ `APPROVED`,
  `active_entitlements_remaining == 7`.
- Submit a client-portal request with `is_external_redistribution=True` $\implies$
  `REDISTRIBUTION_LICENSING_VIOLATION`, `is_approved` False, and the contract's
  `current_active_entitlements` unchanged.
- Submit `use_case_type="EXTERNAL_REDISTRIBUTION"` with
  `is_external_redistribution=False` $\implies$ still
  `REDISTRIBUTION_LICENSING_VIOLATION`.
- Evaluate with `as_of_date` past the expiry $\implies$ `CONTRACT_EXPIRED`.
- Run `python -m unittest discover -s skills/data-vendor-contractual-usage-restriction-tracking/scripts`.

## Related Skills

- `real-time-vs-delayed-data-entitlement-handling`
- `market-data-entitlement-and-licensing-per-venue`
- `market-data-cost-optimization-tiered-subscriptions`
