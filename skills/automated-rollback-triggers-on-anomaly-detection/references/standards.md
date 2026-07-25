# Standards for Deployment Health Metrics

| Metric Category | Metric | Baseline Threshold | Rollback Reason |
|---|---|---|---|
| **Technical** | `latency_ms` | < 50ms | System cannot respond to fast market conditions. |
| **Technical** | `http_5xx_error_rate` | < 1% | Internal server crashes or unhandled exceptions. |
| **Trading** | `order_reject_rate` | < 2% | Logic bug causing the exchange to reject malformed orders. |

## Category
`deployment-ops`