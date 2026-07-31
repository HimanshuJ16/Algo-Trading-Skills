---
name: korea-exchange-krx-api-integration
description: >-
  Quantitative market gateway engine for Korea Exchange (KRX KOSPI / KOSDAQ EXTURE+ engine), enforcing 6-digit stock codes, KRW price tick tiers, and +/- 30% daily price expansion limits.
domain: Global Market Integration & FX
subdomain: South Korean Market Connectivity & KRX Gateway
tags: ["krx", "korea-exchange", "kospi", "kosdaq", "exture-plus", "samsung-electronics", "krw-tick-sizes"]
brokers_frameworks: ["KRX EXTURE+ FIX API", "Koscom Gateway", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing market gateways and order routing logic for the Korea Exchange (KRX KOSPI / KOSDAQ). Order submission to KRX requires strict adherence to South Korean market microstructure rules: 6-digit numeric stock codes (e.g. `005930` Samsung Electronics, `000660` SK Hynix), 7-tier KRW price tick schedules, and $\pm 30\%$ daily price limits.

## Prerequisites

- KRX order payload (`local_code`: `005930`, `side`: `BUY`/`SELL`, `price_krw`, `quantity_shares`, `reference_price_krw`).
- Official KRX EXTURE+ tick size schedule.

## Workflow

1. **6-Digit Stock Code Validation & Zero-Padding**:
   - Verify stock code is 6 numeric digits (`005930`, `000660`, `035420`).
2. **KRX KRW Tick Size Audit**:
   - Compute dynamic tick size $\Delta P$ based on price tier:
     - $P < \text{KRW } 1,000 \implies \Delta P = \text{KRW } 1$.
     - $\text{KRW } 1,000 \le P < \text{KRW } 5,000 \implies \Delta P = \text{KRW } 5$.
     - $\text{KRW } 5,000 \le P < \text{KRW } 10,000 \implies \Delta P = \text{KRW } 10$.
     - $\text{KRW } 10,000 \le P < \text{KRW } 50,000 \implies \Delta P = \text{KRW } 50$.
     - $\text{KRW } 50,000 \le P < \text{KRW } 100,000 \implies \Delta P = \text{KRW } 100$.
     - $\text{KRW } 100,000 \le P < \text{KRW } 500,000 \implies \Delta P = \text{KRW } 500$.
     - $P \ge \text{KRW } 500,000 \implies \Delta P = \text{KRW } 1,000$.
   - Verify order price is an exact integer multiple of $\Delta P$.
3. **Daily Price Limit Audit ($\pm 30\%$)**:
   - Verify order price is within $\pm 30\%$ of reference price.
4. **Audit Report Generation**: Output structured `KrxOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Truncating Leading Zeros**: Passing `5930` instead of 6-digit zero-padded string `005930` for Samsung Electronics.
- **Violating 500 KRW Tick Increments**: Submitting KRW 150,200 for Samsung Electronics (where tick size is KRW 500).
- **Breaching $\pm 30\%$ Daily Price Limits**: Submitting limit orders outside the $\pm 30\%$ price band, causing EXTURE+ engine rejection.

## Verification

- Instantiate `KoreaExchangeKrxApiEngine`. Route Samsung Electronics order (`local_code="5930"` $\to$ zero-padded `005930`, Price $=\text{KRW } 150,000$, Qty $=10$ shares, Ref Price $=\text{KRW } 150,000$) $\implies$ verify tick size $\Delta P = \text{KRW } 500$ and approves `KRX_ORDER_VALIDATED`. Audit Invalid Tick (KRW $150,200$) $\implies$ verify `INVALID_TICK_SIZE`.
- Run `python scripts/test_korea_exchange_krx_api_integration.py`.

## Related Skills

- `japan-exchange-group-jpx-api-integration`
- `exchange-tick-size-regime-tracking`
---
