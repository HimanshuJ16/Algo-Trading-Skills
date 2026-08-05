# Pre-Flight Checklist

- [ ] Is sequence tracking active on all market data channels?
- [ ] Are out-of-order frames buffered up to max buffer limit?
- [ ] Is trading suspended whenever feed state transitions to `DIRTY_SYNC_PENDING`?
- [ ] Are TCP retransmission requests issued for missing sequence ranges?
