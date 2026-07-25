# Standards for Capital Preservation Controls

| Metric | Engineering Standard |
|---|---|
| Independence | The engine must have zero dependencies on the alpha model or strategy logic. |
| Hard Stop | Once tripped, software cannot auto-recover. Manual intervention is required. |
| Order Throttling | Must track orders over a sliding window (e.g., orders per rolling 60 seconds) to catch runaway loops. |
| Error Tracking | Must track consecutive failures (e.g., FIX rejects or TCP timeouts) to detect degraded exchange connectivity. |