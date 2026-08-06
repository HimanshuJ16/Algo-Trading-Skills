# Pre-Flight Checklist

- [ ] Is systemd service configured as `Type=notify` with `WatchdogSec` defined?
- [ ] Is `Restart` set to `on-failure` with `StartLimitBurst` restart rate caps?
- [ ] Is `ExecStartPre` configured to audit API credentials and exchange holidays prior to start?
- [ ] Are watchdog pings emitted at least twice per `WatchdogSec` interval?
- [ ] Is `SIGTERM` handler registered to send `STOPPING=1` and cancel open orders cleanly?
