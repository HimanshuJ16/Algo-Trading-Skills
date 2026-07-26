# Pre-Flight Checklist

- [ ] Is `enable.auto.commit` set to `false` in consumer configuration?
- [ ] Are `on_partitions_revoked` and `on_partitions_assigned` listener callbacks registered?
- [ ] Is thread fencing active so revoked partitions immediately reject new execution calls?
- [ ] Is an idempotency cache active to prevent duplicate execution during rebalances?
