---
name: uk-fca-algorithmic-trading-systems-controls
description: "Institutional regulatory compliance skill for UK FCA MiFID II RTS 6 (Commission Delegated Regulation 2017/589) & FG18/9 guidelines, enforcing pre-trade risk controls, OTR limits, system capacity thresholds, and automated Kill Switches."
domain: Global Regulatory Compliance & Risk Control
subdomain: Algorithmic Systems Governance (UK FCA RTS 6)
tags:
- compliance
- uk-fca
- mifid2-rts6
- fg18-9
- pre-trade-controls
- kill-switch
- price-collars
- otr-limits
- risk-governance
brokers_frameworks:
- fca-handbook
- mifid2-rts6
- quickfix
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when designing, building, or auditing algorithmic trading engines operating in UK/European venues (LSE, Cboe Europe, Turquoise, Aquis) to enforce mandatory **UK FCA RTS 6** and **FG18/9** compliance controls.

This skill provides institutional mechanisms to:
- Enforce mandatory **Pre-Trade Risk Controls** (RTS 6 Article 13): Price Collar checks, Max Order Value/Volume caps, Order-to-Trade Ratio (OTR) limits, and Counterparty Credit Line checks.
- Implement automated **Kill Switch Controls** (RTS 6 Article 12): Sub-second emergency halts and mass-order cancellation.
- Monitor **System Capacity & Stress Thresholds** (RTS 6 Article 14): Automated messaging throughput throttling at 95% capacity limits.
- Maintain immutable **Compliance Audit Logs** for FCA inspection under SM&CR accountability.

## Prerequisites

- Python 3.9+
- Real-time market data feeds (NBBO midpoint prices).
- FIX protocol connection or API access to venue gateway mass-cancel interfaces.

## Workflow

1. **Configure RTS 6 Control Parameters**: Instantiate `RTS6ControlConfig` specifying max price collar % (e.g. 2.5%), max order value GBP (£500,000), max order volume, max OTR ratio (100.0), and capacity kill thresholds (95%).
2. **Track System Capacity & OTR State**: Maintain `SystemCapacityState` updating current message rates, total orders sent, and total trades executed.
3. **Evaluate Pre-Trade Risk Controls**: For every generated order intent (`OrderIntent`), execute `evaluate_pre_trade_controls()`. If compliant (`status == PASSED`), forward to exchange gateway. If non-compliant, block order and log violation type (`PRICE_COLLAR`, `MAX_ORDER_VALUE`, `OTR`, `CAPACITY_EXCEEDED`).
4. **Trigger Emergency Kill Switch**: In the event of runaway algorithm behavior or venue market disruption, invoke `trigger_kill_switch(algo_id, reason)`. The engine immediately blocks subsequent order submissions and initiates venue mass-cancellation.
5. **Reset Kill Switch**: Following compliance sign-off, execute `reset_kill_switch(algo_id)` to restore normal trading.

## Common Pitfalls

- **Bypassing Pre-Trade Controls for Speed**: Placing pre-trade risk checks outside the critical path or disabling them for ultra-low latency execution violates RTS 6 Article 13 and results in immediate FCA regulatory sanctions.
- **Relying Solely on Manual Kill Switches**: Manual GUI buttons take seconds to click. RTS 6 Article 12 mandates automated, event-driven kill switch triggers for runaway algorithms.
- **Ignoring Order-to-Trade Ratio (OTR) Flooding**: Failing to monitor OTR results in venue financial fines for order book spamming and potential exchange disconnects.
- **Unmonitored Credit Line Exceedance**: Submitting orders that exceed pre-allocated clearing counterparty credit limits risks clearing broker default blocks.

## Verification

Run the test suite to validate pre-trade price collars, order value caps, OTR limits, capacity throttling, credit checks, and Kill Switch triggers:

```bash
python -m unittest discover -s skills/uk-fca-algorithmic-trading-systems-controls/scripts
```

## Related Skills

- `uk-senior-managers-regime-algo-accountability`
- `sec-rule-15c3-5-risk-controls-us`
- `kill-switch-and-drawdown-circuit-breakers`
- `us-reg-nms-order-protection-rule-compliance`

