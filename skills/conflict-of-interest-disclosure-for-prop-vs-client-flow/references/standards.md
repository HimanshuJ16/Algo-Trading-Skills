# Standards for Prop vs. Client Flow Conflicts

| Metric | Engineering Standard |
|---|---|
| Rule 5320 Compliance | Prop orders MUST NOT execute ahead of unexecuted customer orders on the same side at equal or better prices without a valid exception. |
| Information Barrier Isolation | Systems utilizing the No-Knowledge Exception MUST maintain distinct `info_barrier_id` tags for Prop vs. Client systems. |
| Order Capacity Tagging | FIX Tag 47 (Rule80A) or equivalent capacity fields MUST be populated on 100% of outbound messages. |