# Standards for Systemd Supervision for Trading Bots

| Systemd Directive | Standard Setting | Rationale |
|---|---|---|
| `Type` | `notify` | Allows bot to signal initialization readiness and watchdog pings. |
| `WatchdogSec` | `30` seconds | Systemd sends SIGABRT if no ping received within 30s. |
| `Ping Frequency` | 15 seconds ($< \frac{1}{2} \text{WatchdogSec}$) | Prevents false-positive timeouts during mild loop latency. |
| `Restart` | `on-failure` | Prevents restarting when bot cleanly exits on deliberate shutdown. |
| `StartLimitBurst` | `5` restarts per 600s | Caps infinite rapid crash loops during upstream broker outages. |
| `MemoryMax` | `1G` (or tailored limit) | Prevents memory leaks from crashing the OS host. |
