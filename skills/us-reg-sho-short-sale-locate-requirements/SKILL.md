---
name: us-reg-sho-short-sale-locate-requirements
description: "Institutional regulatory compliance skill for US SEC Regulation SHO (17 CFR § 242.200-204), validating Rule 200 order markings (LONG, SHORT, SHORT_EXEMPT), Rule 203(b)(1) short sale locates, Rule 201 Short Sale Restriction (SSR / Alternative Uptick Rule) price tests, and locate pool inventory."
domain: US Regulatory Compliance & Market Structure
subdomain: SEC Regulation SHO (Short Sale Regulations)
tags:
- sec-reg-sho
- rule-200
- rule-203
- rule-201
- locate-requirements
- short-sale
- order-marking
- ssr-uptick-rule
brokers_frameworks:
- sec-reg-sho
- finra-cats
- quickfix
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when processing equity short sales, managing prime broker borrow locate pools, or building execution pre-trade compliance gates under **SEC Regulation SHO (17 CFR § 242.200 - 204)** across US equity trading venues.

This skill provides institutional mechanisms to:
- Enforce **Rule 200 Order Marking** (`LONG`, `SHORT`, `SHORT_EXEMPT`) prior to order submission.
- Validate **Rule 203(b)(1) Short Sale Locates**, preventing illegal "naked" short sales by verifying locate identifier validity, symbol alignment, expiration timestamps, and remaining pool capacity.
- Monitor and enforce **Rule 201 Short Sale Circuit Breakers (SSR)**, blocking aggressive short sales at or below the National Best Bid (NBB) once a 10% intraday price drop occurs.
- Support statutory **SHORT_EXEMPT** order tagging for market making, arbitrage, or VWAP benchmark execution exemptions.

## Prerequisites

- Python 3.9+
- Direct API integration with Prime Broker / Clearing Firm Easy-to-Borrow (ETB) and Hard-to-Borrow (HTB) locate feeds.
- Real-time SIP NBBO feed for Rule 201 SSR price test enforcement.

## Workflow

1. **Ingest Prime Broker Locates**: Register granted short sale locates using `grant_locate(locate_id, symbol, quantity, lender_id, ttl_hours)`.
2. **Monitor Rule 201 SSR Triggers**: If a security declines by 10%+ from the prior day's close, trigger the SSR circuit breaker via `trigger_rule_201_ssr(symbol)`.
3. **Submit Order Intent for Pre-Trade Check**: Pass `OrderIntent` (order_id, symbol, marking, quantity, price, NBB price, locate_id) to `validate_order_intent()`.
4. **Validate Rule 203 & Rule 201 Compliance**:
   - **LONG Orders**: Pass immediately.
   - **SHORT Orders**: Verify valid Locate ID, non-expired status, and sufficient remaining locate quantity. If SSR is active, verify order price $> \text{NBB}$.
   - **SHORT_EXEMPT Orders**: Verify valid Locate ID, bypass Rule 201 SSR uptick price test.
5. **Reserve Locate Inventory & Archive Audit**: Deduct order quantity from locate pool and log `RegSHOValidationResult` for FINRA / SEC examination.

## Common Pitfalls

- **Naked Short Selling (Missing Locate ID)**: Submitting short sale orders without a valid, pre-obtained Locate ID violates SEC Rule 203(b)(1) and incurs severe FINRA enforcement penalties.
- **Locate Pool Quantity Double-Counting**: Reusing the same locate quantity across multiple active orders without deducting reserved shares causes locate capacity over-allocation.
- **Executing Short Sales At or Below NBB Under SSR**: Submitting `SHORT` orders at or below NBB when Rule 201 SSR is active violates the Alternative Uptick Rule. Orders MUST be priced strictly $> \text{NBB}$.
- **Mismatched Locate Symbols**: Applying a locate granted for `AAPL` to an order for `TSLA` causes immediate compliance rejection.

## Verification

Run the test suite to validate LONG order passage, naked short rejection, locate quantity tracking, Rule 201 SSR uptick price test enforcement, and SHORT_EXEMPT overrides:

```bash
python -m unittest discover -s skills/us-reg-sho-short-sale-locate-requirements/scripts
```

## Related Skills

- `us-reg-nms-order-protection-rule-compliance`
- `wash-trade-and-spoofing-self-detection`
- `uk-fca-algorithmic-trading-systems-controls`
- `third-party-custody-audit-report-review-cadence`

