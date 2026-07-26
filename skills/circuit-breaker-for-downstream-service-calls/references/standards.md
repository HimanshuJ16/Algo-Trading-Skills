# Standards for Circuit Breakers

| Metric | Engineering Standard |
|---|---|
| Fail-Fast Latency | When the circuit is OPEN, the `CircuitBreakerOpenException` must be thrown in < 1 microsecond. Network I/O MUST NOT be attempted. |
| Specificity | The breaker must only trip on expected infrastructure exceptions (e.g., timeouts, 503 HTTP errors). It MUST NOT trip on business logic exceptions (e.g., HTTP 400 Bad Request, Insufficient Funds), which are deterministic user errors. |
| Half-Open Concurrency | In multi-threaded environments, the Half-Open state must use a thread lock to ensure only *one* test request is allowed through, while other concurrent requests continue to fail fast until the test request succeeds. |
