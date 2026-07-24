# Deep Workflow Reference — systemd-supervision-for-trading-bots

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Systemd Unit File Structure:**
   - Configure systemd unit files (`trading-bot.service`) under `/etc/systemd/system/`.
   - Specify ordering dependencies: `After=network-online.target ws-relay.service`, `Requires=ws-relay.service`.

2. **Restart Policies & Crash-Loop Burst Limits:**
   - Set `Restart=on-failure` (avoid `Restart=always`).
   - Configure burst limits: `StartLimitIntervalSec=600`, `StartLimitBurst=5`, `RestartSec=10` to prevent infinite restart loops from hammering broker APIs.

3. **Pre-Market Healthcheck Script (ExecStartPre):**
   - Execute `run_premarket_healthcheck()` in `ExecStartPre` to verify API secrets, broker connectivity, and exchange holiday status before launching the main trading process.

4. **Systemd Watchdog Integration (sd_notify):**
   - Configure `WatchdogSec=30` and issue periodic `sd_notify("WATCHDOG=1")` keepalive pings from the bot's event loop to catch non-crashing process hangs.

5. **Resource Limits & Journald Logging:**
   - Set explicit process resource bounds (`MemoryMax=1G`, `CPUQuota=150%`).
   - Route stdout/stderr to `journald` for automatic log rotation.

## Failure Modes Observed in Production

- **Unbounded Crash Loops:** Using `Restart=always` without burst limits, causing immediate restart loops that trigger broker IP bans.
- **Fragile Shell Polling:** Using cron shell scripts polling `ps` to restart trading processes, missing hangs and zombie processes.
- **Unverified Pre-Market Startup:** Launching bot main loops on market holidays or with missing API secrets, failing mid-session.
- **Host OOM Spills:** Omitting `MemoryMax` limits, allowing a memory leak in one bot process to crash the entire Linux host.

## Production Implementation Reference

- Reference code: `scripts/supervision_helper.py` (`SystemdSupervisionHelper`, `HealthCheckResult`), `scripts/trading-bot.service`.
- Automated unit tests: `scripts/test_systemd_supervision.py`.
