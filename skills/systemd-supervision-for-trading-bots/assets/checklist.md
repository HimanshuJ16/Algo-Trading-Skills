# Pre-Flight / Sign-off Checklist — systemd-supervision-for-trading-bots

Use this before considering the skill's implementation complete.

- [ ] **Unit File Directives:** Confirm `trading-bot.service` includes `Restart=on-failure`, `WatchdogSec=30`, `StartLimitBurst=5`, and `MemoryMax=1G`.
- [ ] **Pre-Market Healthcheck:** Confirm `run_premarket_healthcheck()` in `ExecStartPre` validates API secrets, broker connectivity, and market holidays.
- [ ] **Systemd Watchdog Integration:** Confirm process emits `sd_notify("WATCHDOG=1")` pings from main event loop.
- [ ] **Resource Isolation:** Confirm memory and CPU limits are configured to protect the host VM.
- [ ] **Automated Testing:** Run `python scripts/test_systemd_supervision.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
