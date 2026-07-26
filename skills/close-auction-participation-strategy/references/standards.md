# Standards for Closing Auction Participation

| Metric | Engineering Standard |
|---|---|
| Cutoff Enforcement | The system MUST strictly reject submitting or modifying MOC/LOC orders after the venue cutoff (e.g. 15:55:00 ET for Nasdaq). |
| Participation Cap | Maximum auction participation MUST NOT exceed 15% of total predicted auction volume to prevent market impact and regulatory scrutiny. |
| Order Type | Liquidity-providing orders MUST use LOC or IO order types to guarantee price protection against extreme closing spikes. |