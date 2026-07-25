# Reference Architecture

The 28 built skills in this repo (of 502 tracked on the global roadmap — see
`docs/ROADMAP_500.md`) assume (and are easiest to apply within) a system shaped
roughly like this — the architecture the skills were extracted from:

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
   │       (owns broker session, tick ingestion)     │     websocket-reconnect-without-duplicate-subscriptions
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

A dashboard/mobile-alert layer (e.g. Next.js + React Native, reading the same shared
Postgres state) typically sits alongside this for monitoring, but doesn't have
dedicated skills in this first pass — see the "Domains not yet covered" note in the
main README if you're contributing.
