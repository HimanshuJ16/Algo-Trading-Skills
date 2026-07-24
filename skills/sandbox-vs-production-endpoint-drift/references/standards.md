# Broker & Framework Coverage — sandbox-vs-production-endpoint-drift

| Broker API | Sandbox Endpoint | Production Endpoint | Common Drift Risks |
|---|---|---|---|
| Alpaca Trading | `https://paper-api.alpaca.markets` | `https://api.alpaca.markets` | Timestamp precision differences |
| Interactive Brokers Web API | Port `4002` (Paper) | Port `4001` (Live) | Extra simulated fields in paper |
| Upstox API v2 | Sandbox mock endpoints | `https://api.upstox.com/v2` | Rate limit header omissions |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with API regression testing, production promotion gate standards, and deployment risk controls.
