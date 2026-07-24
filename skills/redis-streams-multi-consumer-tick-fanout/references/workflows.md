# Deep Workflow Reference — redis-streams-multi-consumer-tick-fanout

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Ingest & Publish Market Data Ticks:**
   - Ingest live tick feed from broker/WebSocket adapter.
   - Publish to Redis Stream `market_ticks` using `XADD` with capped stream size (`MAXLEN ~ 100000`).

2. **Register Consumer Groups:**
   - Create isolated consumer groups per service (`grp_strategy`, `grp_risk`, `grp_db_recorder`) via `XGROUP CREATE`.

3. **Multi-Consumer Fanout Read:**
   - Downstream workers invoke `XREADGROUP` to fetch assigned batch of ticks without interfering with other consumer groups.

4. **Message Acknowledgment:**
   - Execute `XACK` immediately upon successfully processing tick updates to remove entries from the Pending Entries List (PEL).

5. **Stale Pending Entry Recovery:**
   - Periodically execute `XPENDING` to inspect un-ACKed messages.
   - Reassign messages idle for $> 5000\text{ms}$ using `XCLAIM` to recover from worker process crashes.

## Failure Modes Observed in Production

- **Uncapped Stream Memory Exhaustion:** Omitting `MAXLEN ~` on `XADD`, causing Redis RAM to overflow during high-frequency tick bursts.
- **Un-ACKed Message Accumulation:** Forgetting to issue `XACK` after tick processing, filling the PEL and degrading Redis performance.

## Production Implementation Reference

- Reference code: `scripts/redis_tick_fanout.py` (`RedisTickFanoutManager`, `TickData`).
- Automated unit tests: `scripts/test_redis_tick_fanout.py`.
