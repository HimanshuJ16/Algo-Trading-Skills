# Standards for Real-Time Liquidity Risk Monitoring

| Metric | Engineering Standard |
|---|---|
| Days to Liquidate (DTL) Cap | DTL MUST NOT exceed $2.0\text{ days}$ at $10\%$ participation rate. |
| Spread Spike Alarm | Spread widening $> 2.0x$ normal baseline MUST trigger liquidity alarm. |
| Order Book Depth Drop | L2 depth reduction $> 50\%$ MUST trigger depth collapse alert. |