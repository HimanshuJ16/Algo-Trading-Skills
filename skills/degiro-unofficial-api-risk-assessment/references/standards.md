# Broker Integration Standards — degiro-unofficial-api-risk-assessment

| Parameter | Specification | Description |
|---|---|---|
| Base Web URL | `https://trader.degiro.nl` | DEGIRO Web App Endpoint |
| Session Token | `sessionId` | URL/Cookie Session Identifier |
| Account Parameter | `intAccount` | Internal Integer Account Number |
| Dry-Run Endpoint | `/trading/secure/v5/checkOrder` | Order Verification & Fee Calculation |
| Execution Endpoint | `/trading/secure/v5/order` | Order Submission Endpoint |

## Category

`broker-integration` — see top-level `mappings/` directory.
