---
name: deployment-freeze-windows-around-market-events
description: Deployment freeze guard for trading systems — blocks production releases
  inside macro-event windows (FOMC, CPI, NFP, expiry) and timezone-aware daily
  session windows, and enforces a named, two-person break-glass override.
domain: Infrastructure & DevOps
subdomain: CI/CD Governance & Risk Control
tags:
- deployment-freeze
- market-events
- fomc-freeze
- sre-guardrails
- break-glass-protocol
- volatility-control
- ci-cd-governance
brokers_frameworks:
- GitHub Actions
- GitLab CI
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in CI/CD deployment gateways and SRE release controls for production trading systems. Shipping a change minutes before an FOMC decision or into the closing auction puts a fresh binary into the highest-volatility, lowest-recoverability window of the day. This module intercepts a deployment request, decides it against registered freeze windows, and returns a structured audit record.

Two window types are supported:

- **Macro event windows** — one-off, anchored to a release instant with pre/post buffers. The FOMC statement is released at 2:00 p.m. ET ("For release at 2:00 p.m. EDT" on the Fed's own statement PDFs) with the Chair's press conference at 2:30 p.m. ET; BLS publishes CPI and the Employment Situation at 8:30 a.m. ET. Expiry days (triple witching, monthly opex) are registered the same way.
- **Daily session windows** — recurring, anchored to a local wall-clock time in an IANA timezone, so they follow daylight-saving transitions instead of drifting an hour twice a year.

The dual sign-off path records *who* approved. For EU/EEA firms, RTS 6 (Commission Delegated Regulation (EU) 2017/589) Article 11 requires records of any material change to algorithmic trading software identifying when it was made, who made it, **who approved it**, and its nature.

## When NOT to Use

- **As the enforcement point.** This module decides; it does not stop anything. Wire it into the pipeline and fail the job on `is_approved == False`, or it is documentation.
- **As an identity system.** Approver ids are taken at face value. Bind them to authenticated IAM claims upstream — never accept an approver id from an unverified request body (`risk-control-configuration-change-approval-workflow`).
- **As an exchange calendar.** Daily windows assume the standard session unless you supply per-date overrides. NYSE closes at 1:00 p.m. ET on several days a year, so an unfed 16:00 close rule guards an hour when nothing is trading. Feed a real calendar (`global-exchange-holiday-calendar-handling`).
- **As a substitute for a runtime kill switch.** A freeze prevents *new* risk from being introduced; it does nothing about risk already running (`execution-algorithm-kill-switch-integration`).
- **For trading blackouts.** Halting order flow around a release is a different control with different windows — see `global-macro-economic-calendar-integration`.

## Prerequisites

- A macro event schedule with a recorded refresh time (`event_id`, `event_name`, `event_start_epoch_sec`, pre/post buffers). Release dates move: after the 2025 lapse in appropriations BLS shifted the September 2025 Employment Situation from 3 October to 20 November 2025 and never published the October 2025 CPI.
- Session window definitions: IANA timezone, local `HH:MM` anchor, buffers, trading weekdays, and per-date overrides for early closes and holidays.
- An explicit inventory of environment names. Anything not listed as production or exempt is **denied**.
- Python 3.9+ for `zoneinfo`. On Windows the stdlib tz database may be absent — install `tzdata` if `ZoneInfo` raises.

## Workflow

1. **Register windows and record calendar freshness**:
   - Register macro events (`register_freeze_event`) and daily sessions (`register_daily_window`); call `set_calendar_as_of` on every refresh from the upstream source.
   - Duplicate `event_id` is rejected: re-registering a *moved* release under its old id would leave both the stale and the corrected window active.
2. **Classify the environment** — decision point, and the first place this gate historically failed open:
   - Exempt environment → approve.
   - Production environment → continue.
   - Anything else (`PRODCUTION`, `prod`, `PRODUCTION_EU`) → **deny** (`UNKNOWN_ENVIRONMENT_DENIED`). An unrecognised environment is treated as production, never as exempt.
3. **Check calendar freshness** (if `max_calendar_staleness_sec` is configured):
   - Calendar never refreshed, or older than the limit → block (`DEPLOYMENT_BLOCKED_STALE_CALENDAR`). Do not certify "no freeze active" from a calendar that may have missed a rescheduled release.
4. **Evaluate freeze windows**:
   - Collect *every* covering window, not the first match. Bounds are inclusive at both ends.
   - The governing window is the **latest-ending** one, ties broken by label, so the answer does not depend on registration order — and `freeze_ends_epoch_sec` is when the last of them lifts.
   - No covering window and a fresh calendar → approve.
5. **Break-glass, only against a real block**:
   - Requires `is_emergency_hotfix`, both approval booleans, **both approver ids**, and (by default) a justification. Missing any → `MISSING_DUAL_AUTHORIZATION`.
   - The two approver ids must be different people (compared case-insensitively) → same person is `INVALID_DUAL_AUTHORIZATION`. Two booleans one person can both set are not dual authorisation.
   - Approved overrides are logged at WARNING with both identities and recorded in `report.approvers`.
6. **Audit report generation**: emit `DeploymentFreezeAuditReport` and persist it. It is the change record RTS 6 Art. 11 asks for.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Fail-open environment matching**: gating on `environment != "PRODUCTION"` exempts every misspelling. A CI variable of `PRODCUTION` or `prod` deployed straight through an FOMC freeze under the previous logic. Enumerate environments; deny the unknown.
- **NaN and negative inputs silently disabling the gate**: a NaN timestamp compares `False` against every bound and a negative buffer inverts the interval, so both produce "no freeze active" rather than an error. Validate at construction.
- **Fixed-UTC session windows**: 09:30 New York is 13:30 UTC in June and 14:30 UTC in January. A hard-coded UTC window guards the wrong hour for roughly half the year, and mis-handles the spring-forward gap where the local anchor does not exist at all.
- **Ignoring early closes**: on a 1:00 p.m. ET close, a 15:45–16:15 freeze covers a dead market while the actual close volatility runs unguarded.
- **Stale event calendars**: a schedule pulled weeks ago freezes at the wrong hour and blocks deploys for releases that were cancelled. Track `as_of` and fail closed past your refresh SLA.
- **Deploying Right Before FOMC Releases**: shipping routine algorithm updates 10 minutes before a rate decision, causing execution engine failures at peak volatility.
- **Single-person emergency bypasses**: allowing one developer to set both approval flags. Dual sign-off means two named, distinct people, and the record of who approved is itself a regulatory artefact for EU/EEA firms.
- **Reporting only the first matching window**: an operator told "freeze lifts at 15:00" deploys at 15:01 into a still-active press-conference window. Report the latest lift time across all active windows.
- **Blanket Freezes Halting Non-Production Builds**: applying production freezes to staging/research, blocking developer work for no risk reduction.

## Verification

- Register the FOMC statement for the 16–17 June 2026 meeting at 2:00 p.m. ET — June is EDT (UTC−4), so 18:00 UTC — with 60-minute buffers. A `PRODUCTION` request 30 minutes earlier must return `DEPLOYMENT_BLOCKED_FREEZE_ACTIVE` with `freeze_ends_epoch_sec` at 19:00 UTC; both boundary instants are inside the freeze; 61 minutes before is `APPROVED`.
- Add a press-conference window (+30 min, 60-minute post-buffer). A request at 14:30 ET must name the *press conference* as governing and report the later lift time, regardless of registration order.
- Break-glass with two distinct named approvers and a justification → `BREAK_GLASS_HOTFIX_APPROVED` with both ids in `report.approvers`. The same person in both roles → `INVALID_DUAL_AUTHORIZATION`. Missing ids or justification → `MISSING_DUAL_AUTHORIZATION`.
- Environment `PRODCUTION` → `UNKNOWN_ENVIRONMENT_DENIED`, **not** approved.
- A 09:30 `America/New_York` open window must fire at 13:30 UTC in June *and* 14:30 UTC in January, and must not fire at 13:30 UTC in January.
- A `2026-12-24: "13:00"` override must freeze the 1:00 p.m. ET early close and leave 4:00 p.m. ET clear.
- `NaN` timestamps, negative buffers, unknown timezones, and duplicate `event_id`s must raise `DeploymentFreezeError`.
- Run `python -m unittest discover -s skills/deployment-freeze-windows-around-market-events/scripts`.

## Migration from 1.x

Version 2.0.0 tightens two fail-open paths and is deliberately breaking:

- Break-glass now requires `risk_officer_id`, `head_of_trading_id` (distinct), and a justification in addition to the two booleans. Requests that previously passed on booleans alone now return `MISSING_DUAL_AUTHORIZATION`.
- Unknown environment names are denied instead of exempted. Register every environment you deploy to via `production_environments` / `exempt_environments`.

## Related Skills

- `risk-control-configuration-change-approval-workflow`
- `global-macro-economic-calendar-integration`
- `global-exchange-holiday-calendar-handling`
- `execution-algorithm-kill-switch-integration`
- `canary-releases-for-strategy-code-changes`
- `blue-green-deployment-for-live-strategy-updates`
