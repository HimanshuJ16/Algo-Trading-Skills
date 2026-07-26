# Workflows for Consumer Group Rebalance Safety

1. **Partition Assignment**:
   - Call `guard.on_partitions_assigned(partitions)`. Mark assigned partitions as active (`is_active = True`).
2. **Message Processing & Idempotency**:
   - Check if partition is active (`is_active == True`).
   - Deduplicate using `order_id` in idempotency cache.
   - Execute trade / update position state.
3. **Partition Revocation Protocol**:
   - Call `guard.on_partitions_revoked(revoked_partitions)`.
   - **Step A (Fencing)**: Set `is_active = False` immediately.
   - **Step B (Flushing)**: Flush pending execution buffer.
   - **Step C (Sync Commit)**: Synchronously commit offsets to broker.
4. **Rebalance Storm Alert**:
   - Record timestamp of rebalance. If count in rolling 60s window $> 3$, trigger cluster instability alert.
