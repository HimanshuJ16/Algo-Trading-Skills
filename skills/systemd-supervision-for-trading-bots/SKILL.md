---
name: systemd-supervision-for-trading-bots
description: >-
  Use when deploying a trading bot process to a Linux server/VM, to get reliable process supervision, restart behavior, and pre-market health checks — preferred over cron for anything long-running
domain: algorithmic-trading
subdomain: deployment-ops
tags: ["deployment-ops", "systemd"]
brokers_frameworks: ["systemd"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a trading bot needs to run continuously (or reliably at scheduled intervals) on a Linux host. Cron is commonly reached for because it's familiar, but cron is designed for fire-and-forget scheduled jobs, not for supervising a long-running process that needs automatic restart on crash, controlled shutdown, resource limits, and dependency ordering (e.g., don't start the strategy process until the WebSocket relay process is confirmed healthy) — systemd unit files handle all of this natively and are the more correct tool for this job.

## Prerequisites

- Root or sudo access on the deployment host to install unit files under `/etc/systemd/system/`
- The bot process itself should exit cleanly on SIGTERM (handle the signal to close broker sessions, flush logs, and release resources) rather than relying on being killed ungracefully

## Workflow

1. Write a systemd unit file per logical process (e.g., separate units for the WebSocket relay, the strategy engine, and the dashboard backend, rather than one monolithic unit) so each can be independently restarted, monitored, and depend on the others via `After=`/`Requires=` directives.
2. Set `Restart=on-failure` (not `Restart=always` blindly) with a defined `RestartSec` and, critically, a `StartLimitBurst`/`StartLimitIntervalSec` cap — a bot crash-looping without a burst limit will hammer broker login endpoints or exchange connections repeatedly, potentially triggering the broker's own rate-limit/ban mechanisms (see `multi-broker-rate-limit-handling`), turning a code bug into an account-level lockout.
3. Configure `WatchdogSec` with the bot process actively pinging systemd's watchdog (`sd_notify(WATCHDOG=1)`) from within its main loop if the process framework supports it — this catches hangs (process alive but not making progress) that a simple process-exists check would miss, which matters for a bot whose failure mode is silently stalling mid-session rather than crashing outright.
4. Use `ExecStartPre` for a pre-market health check script that verifies broker connectivity, confirms today's date isn't a market holiday, and validates config/secrets are present and correctly formatted, before the main process starts — failing fast on a misconfiguration before market open is far preferable to the bot starting, silently failing on its first API call, and restart-looping through the opening minutes.
5. Set explicit resource limits (`MemoryMax`, `CPUQuota`) appropriate to the bot's expected footprint, so a memory leak or runaway process is contained rather than able to consume the entire host and affect co-located processes (e.g., the dashboard or DB, if run on the same machine).
6. Route logs through `journald` (systemd's default) and configure log rotation/retention explicitly (`journalctl` vacuum settings) rather than letting logs grow unbounded on a long-running VM — this is easy to overlook until a disk-full condition takes down the whole host mid-session.
7. Define ordering dependencies explicitly: the strategy engine unit should declare `After=` and ideally a readiness check against the WebSocket relay unit, so a restart of one doesn't race against the other being ready.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Using cron to "restart the bot every N minutes if not running" via a shell script polling `ps`, which is both fragile (race conditions, no real crash detection) and lacks proper signal handling, log integration, or resource limiting that systemd provides natively.
- Setting `Restart=always` with no burst/interval limit, so a bug causing immediate crash-on-start creates a tight restart loop that can trigger broker-side rate limiting or account lockout.
- Not handling SIGTERM in the bot process, so systemd's stop/restart falls back to SIGKILL after a timeout, preventing graceful session/order-state cleanup.
- Running all bot components (relay, strategy engine, dashboard, DB) with no resource limits on a single host, so one component's memory leak can take down the entire stack.
- Not testing the actual restart/crash-recovery path before going live — assuming "systemd will handle it" without verifying the specific unit file's `Restart=` behavior actually recovers correctly from a simulated crash.

## Verification

- Simulate a crash (`kill -9` the bot process) and confirm systemd restarts it within the configured `RestartSec`, and that the restart correctly reconciles any in-flight state (tying into the reconciliation logic from `order-placement-idempotency`) rather than starting fresh and duplicating actions.
- Confirm a tight crash-loop (simulate by making the bot exit immediately on start) triggers `StartLimitBurst` and stops restarting, rather than looping indefinitely against the broker's login endpoint.
- Confirm `journalctl -u <unit>` shows clean, rotated logs over a multi-day run with no unbounded disk growth.
- Confirm the pre-market health check (`ExecStartPre`) actually blocks the main process from starting when a deliberately-misconfigured secret or invalid config is injected in a test run.

## Related Skills

- `multi-broker-rate-limit-handling`
- `order-placement-idempotency`
- `paper-to-live-promotion-checklist`
