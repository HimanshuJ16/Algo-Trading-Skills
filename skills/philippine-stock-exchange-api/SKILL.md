---
name: philippine-stock-exchange-api
description: >-
  Philippine Stock Exchange (PSE / PSEi) API integration engine validating price-dependent board lot schedules, tick size increments, and static 50% price ceiling/floor circuit breakers.
domain: Global Exchange Integration
subdomain: Emerging Asian Equities Execution
tags: ["pse", "philippine-stock-exchange", "psei", "xts", "board-lot", "tick-size", "asian-markets"]
brokers_frameworks: ["PSEtrade XTS Protocol", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when placing or validating equity orders on the Philippine Stock Exchange (PSE / PSEi) via PSEtrade XTS or broker APIs (e.g. COL Financial, AAA Equities, BDO Securities). PSE enforces a unique price-dependent Board Lot schedule (ranging from 1,000,000 shares for penny stocks to 5 shares for high-priced stocks) and strict tick size increments. Submitting non-conforming quantities or prices leads to instant exchange rejection. This engine validates board lot divisibility, tick increments, and static $\pm 50\%$ price ceiling/floor boundaries.

## Prerequisites

- PSE order request (`symbol`, `side`, `price`, `quantity`, `prev_close_price`).
- Integration config (`api_key`, `environment`).

## Workflow

1. **Board Lot & Tick Size Schedule Lookup**:
   - Query price tier to determine required `(tick_size, min_board_lot)`:
     - $\text{PHP } 0.50 - 4.99 \implies \text{Tick } 0.01, \text{Lot } 1,000$
     - $\text{PHP } 5.00 - 9.99 \implies \text{Tick } 0.01, \text{Lot } 100$
     - $\text{PHP } 10.0 - 49.95 \implies \text{Tick } 0.02 / 0.05, \text{Lot } 100$
     - $\text{PHP } 50.0 - 999.5 \implies \text{Lot } 10$
     - $\text{PHP } \ge 1,000 \implies \text{Lot } 5$
2. **Order Divisibility & Tick Increment Validation**:
   - Check quantity: $\text{quantity} \pmod{\text{min\_board\_lot}} == 0$.
   - Check price: $\text{Price}$ must be an exact multiple of $\text{tick\_size}$.
3. **Static Price Band Audit ($\pm 50\%$)**:
   - Price Ceiling: $P \le 1.50 \times P_{\text{prev\_close}}$.
   - Price Floor: $P \ge 0.50 \times P_{\text{prev\_close}}$.
4. **Audit Report Generation**: Output structured `PSEReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Odd Lots to Main Board**: Placing non-board lot quantities (e.g., 50 shares for a stock trading at $\text{PHP } 8.00$ requiring $100$ shares board lot), causing order rejection or routing to illiquid Odd Lot market.
- **Ignoring Price Tier Transitions**: Order quantity of 1,000 shares is valid at $\text{PHP } 4.90$ (1,000 lot size), but becomes invalid if stock price ticks up to $\text{PHP } 5.05$ (100 lot size requirement).
- **Breaching 50% Price Ceiling/Floor**: Placing limit orders outside the $\pm 50\%$ static price band of previous day's close price.

## Verification

- Instantiate `PhilippineStockExchangeEngine`. Validate 100 shares of SM Investments (`SM`) @ $\text{PHP } 900.00$ ($\text{Lot}=10, \text{Tick}=0.50$) $\implies$ verify `ORDER_VALID_COMPLIANT`. Submit odd lot quantity 5 shares $\implies$ verify `INVALID_BOARD_LOT` rejection.
- Run `python scripts/test_philippine_stock_exchange_api.py`.

## Related Skills

- `korea-exchange-krx-api-integration`
- `japan-exchange-group-jpx-api-integration`
---
