# Standards for EU Market Abuse Regulation (MAR) Surveillance

| Metric | Engineering Standard |
|---|---|
| Wash Trade Zero Tolerance | Self-execution trades (`buyer_id == seller_id`) MUST be flagged immediately. |
| Spoofing Cancel Threshold | Order cancellation rates $> 90\%$ with $< 100\text{ms}$ lifetime MUST trigger `SPOOFING_ALERT`. |
| STOR Filing Timeliness | STOR reports MUST be generated and filed "without delay" upon detection. |
