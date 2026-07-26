# Standards for Consumer Group Rebalance Safety

| Metric | Engineering Standard |
|---|---|
| Auto-Commit Policy | Automatic offset commits MUST be disabled (`enable.auto.commit = false`) on all trading event consumers. |
| Synchronous Revocation Commit | Offset commits in `on_partitions_revoked` MUST be synchronous to ensure completion before reassignment. |
| Partition Fencing | Processing trade events on a revoked partition MUST immediately throw an exception. |
