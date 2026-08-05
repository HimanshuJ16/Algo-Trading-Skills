# Standards for Smart Order Routing Across Venues

| Rule / Parameter | Standard Requirement |
|---|---|
| Reg NMS Rule 611 | Child orders MUST NOT execute at prices inferior to NBBO (trade-through protection). |
| Standard Taker Fee Cap | SEC Rule 610 caps exchange access fees at $0.0030 per share ($30/10k shares). |
| Concurrent Routing | Child orders to multiple venues MUST be sent concurrently to minimize latency leakage. |