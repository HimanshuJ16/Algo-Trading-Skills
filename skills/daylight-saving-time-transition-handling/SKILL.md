---
name: daylight-saving-time-transition-handling
description: Quantitative market schedule and timezone engine for handling Daylight
  Saving Time (DST) shifts across US, EU, and Asian exchanges, calculating UTC session
  opens, and detecting 2-week desynchronization windows.
domain: Data Management Global
subdomain: Timezone & Session Scheduling
tags:
- dst-transition
- timezone-handling
- utc-normalization
- iana-timezones
- session-scheduling
- cross-border-desync
- market-hours
brokers_frameworks:
- Python zoneinfo
- IANA Time Zone Database
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in global market data pipelines, cross-border arbitrage algorithms, and session schedulers operating across US (NYSE/Nasdaq), European (LSE/XETRA), and Asian (TSE/HKEX) exchanges. US exchanges shift to Daylight Saving Time on the second Sunday in March, while European exchanges shift on the last Sunday in March, creating a **2-week desynchronization window** where the US-EU market overlap shifts by 1 hour. This module normalizes all market sessions to UTC nanosecond timestamps and dynamically recalibrates trading schedules.

## Prerequisites

- IANA Timezone names (`America/New_York`, `Europe/London`, `Asia/Tokyo`).
- Exchange trading hours in local time (`local_open_time` e.g. "09:30", `local_close_time` e.g. "16:00").

## Workflow

1. **Exchange Schedule Registration**:
   - Register exchange IANA timezone and local session times.
2. **UTC Session Calculation**:
   - For target date $D$, parse local open/close in exchange IANA timezone (`zoneinfo.ZoneInfo`).
   - Convert to UTC datetime and UTC nanosecond epoch integers ($t_{\text{ns}}$).
3. **Cross-Border Desynchronization Audit**:
   - Compare UTC open/close deltas between US (`America/New_York`) and EU (`Europe/London`).
   - Detect 2-week March/October desynchronization windows ($\Delta t_{\text{overlap}} \neq \text{Standard Overlap}$).
4. **Audit Report Generation**: Output structured `DstTransitionAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hardcoding UTC Offsets**: Using fixed `-5` hours for US EST or `+0` for UK GMT, failing when DST shifts to `-4` (EDT) or `+1` (BST).
- **Ignoring 2-Week US-EU Desync Windows**: Assuming US and EU shift to DST on the same weekend, mis-aligning cross-border relative value algorithms in March and October.
- **Duplicate/Skipped Hourly Bars in Local Time**: Aggregating time-series data using local timestamps during "Fall Back" (duplicate 2 AM hour) or "Spring Forward" (missing 2 AM hour).

## Verification

- Instantiate `DstTransitionHandlerEngine`. Register NYSE (`America/New_York`, 09:30-16:00) and LSE (`Europe/London`, 08:00-16:30). Evaluate UTC session open on March 15, 2026 (US in EDT -4, EU in GMT 0 -> 2-week desync window!). Verify NYSE open UTC = `13:30:00Z` and LSE open UTC = `08:00:00Z` (Overlap shift detected). Evaluate on April 1, 2026 (both in DST) and verify standard alignment.
- Run `python scripts/test_daylight_saving_time_transition_handling.py`.

## Related Skills

- `multi-timezone-session-scheduling`
- `cross-vendor-timestamp-precision-reconciliation`
---
