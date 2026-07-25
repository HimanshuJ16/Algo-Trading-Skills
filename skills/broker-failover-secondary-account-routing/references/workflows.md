# Failover Workflow Sequence

```mermaid
sequenceDiagram
    participant Strategy
    participant Router as FailoverRouter
    participant Primary
    participant Secondary

    Note over Router, Primary: State: CLOSED (Normal)
    Strategy->>Router: submit_order(AAPL)
    Router->>Primary: place_order(AAPL STK SMART)
    Primary-->>Router: Error (HTTP 503)
    Router->>Secondary: place_order(AAPL)
    Secondary-->>Router: FILLED
    Router-->>Strategy: OrderResult (Secondary)

    Note over Router, Primary: Failures > Max. State: OPEN
    Strategy->>Router: submit_order(MSFT)
    Router->>Secondary: place_order(MSFT)
    Secondary-->>Router: FILLED
    Router-->>Strategy: OrderResult (Secondary)

    Note over Router, Primary: Timeout Exceeded. State: HALF_OPEN
    Strategy->>Router: submit_order(GOOG)
    Router->>Primary: place_order(GOOG STK SMART)
    Primary-->>Router: FILLED
    Router-->>Strategy: OrderResult (Primary)
    Note over Router, Primary: Probe Success. State: CLOSED
```

## Explanation
1. **Normal Flow**: Orders go to the Primary Broker.
2. **Tripping the Breaker**: If consecutive errors occur, the internal state shifts to OPEN.
3. **Failover Execution**: While OPEN, all subsequent orders bypass the Primary and are mapped/sent to the Secondary.
4. **Recovery**: After a timeout, the state becomes HALF_OPEN. The next order acts as a probe. If successful, state goes back to CLOSED.
