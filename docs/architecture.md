# Reference Architecture

The skills in this repo span 16 engineering domains (listed by domain in
[`ROADMAP_500.md`](ROADMAP_500.md)) and assume a system shaped roughly like this — the
architecture they were extracted from:

```
                 ┌─────────────────────┐
                 │   Broker(s)         │
                 │  Fyers / Breeze /   │
                 │  Kite / Upstox / ...│
                 └──────────┬──────────┘
                            │ REST (auth, orders) + WebSocket (ticks)
                            ▼
   ┌─────────────────────────────────────────────────┐
   │              WebSocket Relay Process            │  ← producer-consumer-tick-pipeline
   │       (owns broker session, tick ingestion)     │     websocket-subscription-reconciliation-after-reconnect
   └────────────────────────┬────────────────────────┘
                            │ pub-sub / queue (Redis or in-process)
                            ▼
   ┌────────────────────────────────────────────────┐
   │             Strategy / Signal Engine           │  ← tick-buffering-burst-handling
   │   (feature computation, ML inference, signals) │     backpressure-drop-degrade-policy
   │                                                │     feature-engineering-without-leakage
   │                                                │     offline-train-online-infer-deployment
   │                                                │     model-staleness-detection
   └────────────────────────┬───────────────────────┘
                            │ proposed orders
                            ▼
   ┌────────────────────────────────────────────────┐
   │              Risk Module (independent)         │  ← kill-switch-and-drawdown-circuit-breakers
   │   (position/drawdown/correlation limits — has  │    correlation-aware-exposure-limits
   │    veto power over the strategy engine)        │
   └────────────────────────┬───────────────────────┘
                            │ approved orders
                            ▼
   ┌────────────────────────────────────────────────┐
   │           Order Placement + Idempotency        │  ← order-placement-idempotency
   │        (broker auth, rate limiting, ledger)    │     token-lifecycle-live-probing
   │                                                │     multi-broker-rate-limit-handling
   │                                                │     headless-broker-auth-patterns
   └────────────────────────────────────────────────┘

   Shared state: PostgreSQL/NeonDB (positions, ledger, logs)
   Supervision:  systemd units per process ← systemd-supervision-for-trading-bots
   Validation gate before any of the above touches real capital:
                 backtesting-methodology category (lookahead-bias-elimination,
                 walk-forward-validation-setup, execution-realistic-simulation)
                 → paper-to-live-promotion-checklist
```

A dashboard and alerting layer reading the same shared state typically sits alongside
this for monitoring. The operational side of it is covered by
`log-aggregation-and-centralized-observability`, `structured-logging-for-post-incident-forensics`
and `on-call-rotation-and-escalation-for-trading-systems`; the front end itself is out of
scope for this library.
