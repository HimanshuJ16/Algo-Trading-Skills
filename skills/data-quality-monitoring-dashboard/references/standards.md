# Standards for Data Quality Monitoring Dashboard

## What is standardised, and what is not

The four record-level pillars map to the **DAMA-DMBOK** data quality dimensions
(completeness, timeliness, accuracy, uniqueness). **Liveness** is an operational
feed-health pillar specific to streaming market data; it is not a DAMA dimension.

No regulator, exchange, or standards body publishes a numeric "market data DQ score",
a required pillar weighting, or a mandatory failover threshold. Every number in the
table below is a **configurable engineering default**, not an external requirement.
Do not present the composite score to a regulator or vendor as a compliance metric,
and do not treat a breach of these defaults as a rule breach.

| Parameter | Default | Status |
|---|---|---|
| `min_healthy_score` (HEALTHY floor) | 85.0 | Engineering default — tune per vendor and strategy sensitivity. |
| `critical_failover_score` (failover trigger) | 70.0 | Engineering default. Must be strictly below `min_healthy_score`. |
| `latency_zero_score_ms` (timeliness floor) | 500.0 ms | Engineering default — set to the tick-to-ingest budget of the consuming strategy, not to a published SLA. |
| Pillar weights (comp/time/acc/uniq/live) | 0.25 / 0.25 / 0.25 / 0.15 / 0.10 | Engineering default. Must sum to 1.0. |
| Penalty factors (null / outlier / duplicate) | 2.0 / 5.0 / 2.0 | Engineering default. Outliers are weighted most steeply because one mispriced tick can drive an execution algorithm to cross the spread. |

Vendor-contracted latency or uptime SLAs, where they exist, come from the market data
agreement itself. Read the contract rather than assuming a figure from this table.

## Adjacent regulatory context (EU)

MiFID II RTS 6 — [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng),
Article 16 — requires investment firms engaged in algorithmic trading to monitor all
algorithmic trading activity in real time, and states that "[r]eal-time alerts shall be
generated within five seconds after the relevant event" (Article 16(5)).

**Scope caveat:** Article 16 governs monitoring of *algorithmic trading activity* for
signs of disorderly trading. It does **not** impose a market data feed quality standard,
and it prescribes no DQ score. It is cited here only because firms in scope of RTS 6
typically reuse the same alerting path for feed-health alerts, which makes the
five-second alert budget a practical design target for this engine's dispatch latency.
Verify applicability against your own jurisdiction and authorisation status.

## Reference

- DAMA International, *DAMA-DMBOK: Data Management Body of Knowledge* (2nd ed.),
  Chapter 13 (Data Quality) — source of the completeness/timeliness/accuracy/uniqueness
  dimension taxonomy. See <https://www.damadmbok.org/>.
