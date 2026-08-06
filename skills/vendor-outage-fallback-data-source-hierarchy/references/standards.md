# Institutional Market Data Vendor Outage & Fallback Standards

## 1. Prioritized Data Source Hierarchy Matrix
| Hierarchy Priority | Data Source Category | Primary Protocol | Typical Latency SLA | Staleness Threshold |
| :--- | :--- | :--- | :--- | :--- |
| **Priority 1 (Primary)** | Direct Exchange Feed (ITCH/FIX) | UDP Multicast / Direct FIX | $< 1\ \text{ms}$ | $\le 2.0\ \text{seconds}$ |
| **Priority 2 (Secondary)**| Consolidated Aggregator (B-PIPE/Refinitiv) | Enterprise WebSocket / TCP | $5 - 50\ \text{ms}$ | $\le 5.0\ \text{seconds}$ |
| **Priority 3 (Tertiary)** | REST/WebSocket Cloud Feed (Polygon/IEX) | Public WebSocket / REST | $100 - 500\ \text{ms}$ | $\le 10.0\ \text{seconds}$ |
| **Priority 4 (Fallback)** | Synthetic Cache / Historical Interpolation | In-Memory Local Redis / RAM | $< 0.1\ \text{ms}$ | Last Available Price |

---

## 2. Health Monitoring & Failure Condition Thresholds
A data source node is degraded from `HEALTHY` to `STALE`, `ERROR`, or `DISCONNECTED` based on:

1. **Staleness Condition**:
   $$\Delta t_{\text{stale}} = t_{\text{now}} - t_{\text{heartbeat}} > \text{max\_staleness\_seconds}$$

2. **Error Threshold Exceedance**:
   $$\text{Error Count} \ge \text{max\_error\_threshold}$$

3. **Engine State Transitions**:
   - `PRIMARY_ACTIVE`: Priority 1 source healthy.
   - `FAILOVER_ACTIVE`: Priority 1 unhealthy; active source is Priority 2 or Priority 3.
   - `SYNTHETIC_CACHE_ACTIVE`: All live data sources unhealthy; serving cached ticks (`is_synthetic=True`).
   - `ALL_SOURCES_DOWN`: Live feeds down and no synthetic cache available (System Exception).

---

## 3. Anti-Flapping Recovery Cooling Equation
When Primary feed recovers after a failover event, restoration is delayed to prevent rapid connection flapping:

$$\text{Restore Primary} \iff \left( \text{Status}_{\text{Primary}} = \text{HEALTHY} \right) \;\land\; \left( t_{\text{now}} - t_{\text{failover}} \ge T_{\text{cooling}} \right)$$

Where $T_{\text{cooling}}$ is the mandatory recovery cooling duration (default 30 seconds).

