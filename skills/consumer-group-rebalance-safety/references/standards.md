# Real-Time Architecture Standards — consumer-group-rebalance-safety

| Event Hook | Trigger Condition | Action |
|---|---|---|
| `on_partitions_revoked` | Consumer node leaving/rebalancing | Pause fetch; flush in-flight batch; commit offset |
| `on_partitions_assigned` | Rebalance assignment complete | Initialize partition state; resume consumption |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
