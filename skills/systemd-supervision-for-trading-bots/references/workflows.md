# Workflows for Systemd Supervision for Trading Bots

1. **Unit File Provisioning**:
   - Save `/etc/systemd/system/trading-bot.service` with `Type=notify`, `WatchdogSec=30`, and resource caps (`MemoryMax=1G`).
2. **ExecStartPre Health Check**:
   - Run healthcheck script verifying secrets, exchange calendar, and broker connectivity prior to main process start.
3. **Notify Protocol Integration**:
   - Send `READY=1` on initialization and `WATCHDOG=1` every 15 seconds during active event loop.
4. **Shutdown Signal Handling**:
   - Intercept `SIGTERM` from systemd, cancel active open limit orders, and transmit `STOPPING=1`.
