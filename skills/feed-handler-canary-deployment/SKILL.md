---
name: feed-handler-canary-deployment
description: Use when releasing feed handler code updates to canary-route a controlled
  fraction of ticker symbols (5-10%) to the new release, execute comparative price
  audit against baseline feeds, and trigger auto-rollback on error spikes.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- canary-deployment
- feed-handler
- symbol-routing
- comparative-audit
- auto-rollback
- zero-downtime
brokers_frameworks:
- Canary Feed Router
- Python Real-Time Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when upgrading low-latency market data feed handlers (e.g., updating FIX/ITCH parser libraries or WebSocket connection logic). Deploying unvalidated updates to 100% of traded symbols risks pipeline-wide tick dropping or incorrect price decoding. A canary router routes a fraction of symbols (e.g. 5% of universe) to the new $V_{\text{canary}}$ release, comparing tick outputs against stable $V_{\text{stable}}$ baseline before promoting to 100%.

## Prerequisites

- Stable baseline feed handler instance ($V_{\text{stable}}$) and candidate canary instance ($V_{\text{canary}}$).
- Canary allocation percentage (e.g., $10\%$) or explicit canary symbol whitelist.

## Workflow

1. **Allocate Canary Symbol Subset**:
   - Determine canary symbol set using consistent hashing (`hash(symbol) % 100 < canary_percentage`) or explicit list (e.g. `["AAPL", "MSFT"]`).

2. **Route Traffic & Execute Shadow Auditing**:
   - For canary symbols, route live feed traffic to $V_{\text{canary}}$ while optionally shadow-duplicating ticks to $V_{\text{stable}}$ for comparative diffing.

3. **Perform Comparative Price & Latency Audit**:
   - Verify tick price agreement ($|P_{\text{canary}} - P_{\text{stable}}| / P < 0.0001$) and zero sequence gaps.

4. **Promote or Trigger Auto-Rollback**:
   - If error rate $= 0$ over observation period $T_{\text{canary}}$, ramp allocation $10\% \to 50\% \to 100\%$.
   - If error threshold is breached ($> 0.1\%$ errors), trip rollback breaker and revert 100% traffic to $V_{\text{stable}}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Canary Symbol Selection Bias**: Testing canary deployment on liquid mega-caps only, missing parser bugs present in illiquid stock price formats.
- **Lacking Automated Rollback**: Requiring manual engineer intervention during a canary memory leak, delaying rollback by minutes.
- **Shared Mutable State Contention**: Allowing canary and baseline handlers to mutate shared in-memory orderbook state simultaneously.

## Verification

- Allocate 10% canary traffic and verify symbol distribution match.
- Inject error into canary feed and verify auto-rollback trigger to $100\%$ baseline.
- Run `python scripts/test_canary_router.py` and confirm 100% pass rate.

## Related Skills

- `market-data-feed-arbitration-across-vendors`
- `broker-api-changelog-diffing-tool`
- `graceful-shutdown-draining-in-flight-ticks`
---
