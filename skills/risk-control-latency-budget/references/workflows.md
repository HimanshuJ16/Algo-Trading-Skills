# Risk-Control Latency Workflow

1. Define the required end state: decision, local dispatch, broker acknowledgement, cancel acknowledgement, or effective exposure reduction.
2. Instrument every boundary under one synchronized timestamp policy. Preserve invalid ordering as an error or uncertain result.
3. Budget ingestion, evaluation, queueing, dispatch, acknowledgement, retries, and fail-safe actuation separately.
4. Segment reports by control, venue, account, strategy, session, and deployment version. Declare percentile window and minimum sample count.
5. Treat unsynchronized-clock measurements as unhealthy evidence. Investigate skew, replay, delayed producers, and clock-source changes before certifying an SLA.
6. On breach, inspect stage breakdown, queue depth, CPU/GC, dependency latency, rate limits, retries, and broker response; verify fallback completion.
7. Fault-test stalled queues, clock skew, slow stores, stale market data, network loss, broker throttling, and cancellation acknowledgement outside production.
