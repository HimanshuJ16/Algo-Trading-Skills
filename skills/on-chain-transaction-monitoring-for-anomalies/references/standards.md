# Standards for On-Chain Transaction Monitoring for Anomalies

| Metric | Engineering Standard |
|---|---|
| Sanctions Screening | OFAC SDN address interactions MUST trigger immediate transaction block. |
| High Risk Threshold | Composite Risk Score $\ge 70$ MUST trigger `HIGH_RISK_BLOCK`. |
| Gas Price Limit | Gas spikes $> 5\times$ baseline MUST trigger anomaly review. |