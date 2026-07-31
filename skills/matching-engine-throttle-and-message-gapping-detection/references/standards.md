# Standards for Matching Engine Monitoring

| Metric | Engineering Standard |
|---|---|
| Throttle Warning Threshold | Client systems MUST warn at 80% of exchange MPS limit. |
| Hard Throttle Block | Outbound orders MUST be blocked when reaching 100% MPS limit. |
| Sequence Gap Action | Inbound sequence gaps MUST trigger an immediate Retransmit Request. |
