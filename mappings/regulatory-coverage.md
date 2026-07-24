# Regulatory & Compliance Touchpoints

Skills in this repo are engineering practices, not legal or compliance advice. This file
is a pointer index of where a skill's subject matter intersects with regulatory or
exchange requirements an implementer should independently verify — not a substitute
for that verification.

| Area | Relevant skills | What to check independently |
|---|---|---|
| Algorithmic trading approval / tagging | `paper-to-live-promotion-checklist`, `kill-switch-and-drawdown-circuit-breakers` | Exchange/broker requirements for algo order tagging and approval (e.g. SEBI algo-trading circulars for Indian markets; equivalent requirements in your jurisdiction) |
| Risk controls mandated at broker/exchange level | `kill-switch-and-drawdown-circuit-breakers`, `correlation-aware-exposure-limits` | Exchange-mandated price bands, position limits, and circuit filters that apply independently of any bot-level controls |
| Order/trade record retention | `order-placement-idempotency`, `paper-to-live-promotion-checklist` | Local recordkeeping/audit-trail requirements for algorithmic order flow in your jurisdiction |
| Transaction charges and taxes | `execution-realistic-simulation` | Current STT, exchange transaction charges, stamp duty, and GST rates (these change periodically — do not hardcode rates from this repo's examples without checking current values) |
| Data usage / market data licensing | `producer-consumer-tick-pipeline`, `tick-buffering-burst-handling` | Broker/exchange market-data licensing terms for redistribution, storage, or third-party display of tick data |

## Disclaimer

Nothing in this repo constitutes legal, tax, or regulatory advice. Regulations governing
algorithmic trading vary by jurisdiction and change over time. Consult a qualified
professional and your broker/exchange's current documentation before deploying a live
trading system.
