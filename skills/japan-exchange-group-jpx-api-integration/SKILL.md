---
name: japan-exchange-group-jpx-api-integration
description: >-
  Quantitative market gateway engine for Japan Exchange Group (JPX / Tokyo Stock Exchange TSE arrowhead 3.0), enforcing 4-digit ticker codes, JPY price tick tiers, and 100-share Board Lots.
domain: Global Market Integration & FX
subdomain: Japanese Market Connectivity & TSE Arrowhead Gateway
tags: ["jpx", "japan-exchange", "tse", "arrowhead", "tokyo-stock-exchange", "board-lot", "tick-sizes"]
brokers_frameworks: ["TSE Arrowhead 3.0 FIX API", "J-GATE 3.0 Derivatives Gateway", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing market gateways and order routing algorithms for Japan Exchange Group (JPX / Tokyo Stock Exchange TSE arrowhead 3.0). Order submission to the TSE requires strict compliance with Japanese market microstructure rules: 4-digit local stock codes (e.g. `7203` Toyota, `6758` Sony), JPY price-tier dependent tick sizes, 100-share Board Lot (単元株) sizing, and dynamic daily price limits.

## Prerequisites

- TSE order payload (`local_code`: `7203`, `side`: `BUY`/`SELL`, `price_jpy`, `quantity_shares`, `reference_price_jpy`).
- Official TSE arrowhead 3.0 tick size schedule.

## Workflow

1. **4-Digit Local Stock Code Validation**:
   - Verify local stock code is 4 digits (`7203`, `6758`, `9984`).
2. **TSE JPY Tick Size Audit**:
   - Compute dynamic tick size $\Delta P$ based on price tier:
     - $P < \text{JPY } 1,000 \implies \Delta P = \text{JPY } 1.0$.
     - $\text{JPY } 1,000 \le P < \text{JPY } 3,000 \implies \Delta P = \text{JPY } 1.0$.
     - $\text{JPY } 3,000 \le P < \text{JPY } 5,000 \implies \Delta P = \text{JPY } 5.0$.
     - $\text{JPY } 5,000 \le P < \text{JPY } 10,000 \implies \Delta P = \text{JPY } 10.0$.
     - $P \ge \text{JPY } 10,000 \implies \Delta P = \text{JPY } 50.0$.
   - Verify order price is an exact integer multiple of $\Delta P$.
3. **Board Lot Sizing (1 Unit = 100 Shares)**:
   - Verify order quantity is an exact multiple of 100 shares ($1\text{ Board Lot}$).
4. **Daily Price Expansion Limit Audit**:
   - Verify price is within $\pm 20\%$ daily price limits.
5. **Audit Report Generation**: Output structured `JpxOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Odd-Lot Orders**: Submitting non-100-share multiples to TSE arrowhead, causing instant order rejection.
- **Violating JPY Tick Tiers**: Submitting JPY 3,002 for a stock priced above JPY 3,000 (where tick size is JPY 5.0).
- **Misformatting Stock Codes**: Passing ISIN strings instead of 4-digit TSE local stock codes (`7203`).

## Verification

- Instantiate `JpxStockExchangeApiEngine`. Route Toyota order (`local_code="7203"`, Price $=\text{JPY } 2,500$, Qty $=500$ shares / 5 Lots, Ref Price $=\text{JPY } 2,500$) $\implies$ verify tick size $\Delta P = \text{JPY } 1.0$, confirms 5-lot size multiplier, and approves `JPX_ORDER_VALIDATED`.
- Run `python scripts/test_japan_exchange_group_jpx_api_integration.py`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
---
