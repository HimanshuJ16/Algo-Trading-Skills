# Broker Integration Standards — tastytrade-api-integration

| Parameter | Specification | Description |
|---|---|---|
| Certification Base URL | `https://api.cert.tastyworks.com` | Sandbox / Certification Environment |
| Production Base URL | `https://api.tastyworks.com` | Live Production Environment |
| Auth Scheme | Header Session Token | `Authorization: {session-token}` |
| OCC Format | 21 characters | `TICKER(6)YYMMDD(6)C/P(1)STRIKE*1000(8)` |
| Order Legs Endpoint | `/accounts/{account}/orders` | Complex Multi-Leg Order Dispatch |

## Category

`broker-integration` — see top-level `mappings/` directory.
