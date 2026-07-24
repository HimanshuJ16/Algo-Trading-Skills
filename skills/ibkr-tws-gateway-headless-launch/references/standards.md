# Broker & Framework Coverage — ibkr-tws-gateway-headless-launch

| Environment / Service | Default API Port | Protocol | Reset Schedule |
|---|---|---|---|
| IB Gateway (Paper) | `4002` | TCP Socket / TWS API | Daily ~23:45 EST |
| IB Gateway (Live) | `4001` | TCP Socket / TWS API | Daily ~23:45 EST |
| Trader Workstation TWS (Paper) | `7497` | TCP Socket / TWS API | Daily configurable |
| Trader Workstation TWS (Live) | `7496` | TCP Socket / TWS API | Daily configurable |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with automated trading daemon supervision, containerized bot operations, and network socket reliability standards.
