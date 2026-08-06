---
name: systemd-supervision-for-trading-bots
description: >-
  Production-grade systemd process supervision engine for algorithmic trading bots implementing sd_notify watchdog keepalive pings, pre-market healthchecks (ExecStartPre), burst limit restart controls, and systemd unit file validation.
domain: Production Operations & Infrastructure
subdomain: Process Supervision & High Availability
tags: ["systemd", "process-supervision", "watchdog", "sd-notify", "pre-market-checks", "trading-bot"]
brokers_frameworks: ["Linux Systemd", "Python Socket", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying production trading bots on Linux host infrastructure managed by `systemd`. Unmanaged background processes or basic cron jobs lack hardware resource quotas, automatic restart limits, and health pings. This engine provides `SystemdSupervisionHelper` for sending `sd_notify` socket keepalive pings (`WATCHDOG=1`), validating unit file configurations (e.g. `Type=notify`, `WatchdogSec=30`, `Restart=on-failure`, `MemoryMax=1G`), and executing pre-market health checks (`ExecStartPre`) before initiating active trading.

## Prerequisites

- Linux host running `systemd` daemon with `NOTIFY_SOCKET` environment variable (or local unit file for static audit).

## Workflow

1. **Systemd Service Unit Construction**:
   - Set `Type=notify`, `WatchdogSec=30`, `Restart=on-failure`, `StartLimitIntervalSec=600`, `StartLimitBurst=5`.
2. **Pre-Market Health Check Execution (`ExecStartPre`)**:
   - Run `SystemdSupervisionHelper.run_premarket_healthcheck(secrets, broker_conn_fn, is_holiday_fn)` to verify API key presence, broker socket ping, and exchange holiday schedule.
3. **Bot Initialization & Ready Notification**:
   - After establishing broker WS sessions, send `notify_ready("Bot initialized")` (`READY=1`).
4. **Main Loop Watchdog Keepalive**:
   - In trading event loop, issue periodic `notify_watchdog()` (`WATCHDOG=1`) every $T < \frac{1}{2} \text{WatchdogSec}$ seconds.
5. **Clean Termination**:
   - Catch `SIGTERM` / `SIGINT` signals and send `notify_stopping()` (`STOPPING=1`) before closing order sockets.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **`Restart=always` Infinite Crash Loops**: Using `Restart=always` without `StartLimitBurst`, causing infinite rapid restart loops during broker outage and exhausting API rate limits or triggering exchange IP bans. Use `Restart=on-failure` with `StartLimitBurst=5`.
- **Missed Watchdog Pings During Blocking I/O**: Executing long blocking synchronous REST calls in the main event loop, causing `WatchdogSec` timeout and unnecessary systemd process SIGABRT kills. Issue watchdog pings in a dedicated async timer or thread.
- **Notifying Ready Before Broker Connection**: Sending `READY=1` before API authentication succeeds, causing systemd to treat a broken service as healthy.

## Verification

- Inspect `trading-bot.service` using `SystemdSupervisionHelper.validate_unit_file_content()` $\implies$ verify `valid = True`. Run pre-market health check with missing secrets $\implies$ verify `passed = False`. Issue `notify_watchdog()` without `NOTIFY_SOCKET` $\implies$ verify graceful fallback returns `False`.
- Run `python scripts/test_systemd_supervision.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `data-quality-monitoring-dashboard`
---
