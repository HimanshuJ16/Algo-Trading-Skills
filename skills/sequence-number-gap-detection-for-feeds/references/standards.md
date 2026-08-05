# Standards for Sequence Number Gap Detection for Feeds

| State | Condition | Trading Authorization |
|---|---|---|
| SYNCED | All contiguous sequence numbers processed | ENABLED |
| DIRTY_SYNC_PENDING | Sequence gap detected ($S_{\text{incoming}} > S_{\text{expected}}$) | DISABLED / HOLD |
| RECOVERING | Ingesting missing retransmission frames | DISABLED / HOLD |
