# Broker Integration Standards — saxo-bank-openapi-integration

| Parameter | Specification | Description |
|---|---|---|
| SIM Gateway Base URL | `https://gateway.saxobank.com/sim/openapi` | Simulation / Sandbox Environment |
| LIVE Gateway Base URL | `https://gateway.saxobank.com/openapi` | Production Live Environment |
| Auth Scheme | OAuth2 Bearer Header | `Authorization: Bearer {TOKEN}` |
| Instrument Lookup | `/ref/v1/instruments` | UIC Universal Instrument Code Lookup |
| Order Placement | `/trade/v1/orders` | Multi-asset Order Routing Endpoint |
| Portfolio Positions | `/port/v1/positions` | Position Snapshot & P&L Endpoint |

## Category

`broker-integration` — see top-level `mappings/` directory.
