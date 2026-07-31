---
name: moscow-exchange-moex-api-integration
description: >-
  Moscow Exchange (MOEX) API integration engine supporting ISS REST market data queries, FIX/TWIME low-latency order routing, TQBR/CETS/FORTS board rules, and price collaring.
domain: Exchange Integrations Global
subdomain: Eastern European Exchanges & MOEX Integration
tags: ["moex", "moscow-exchange", "iss-api", "twime", "tqbr", "forts", "fix-protocol", "emerging-markets"]
brokers_frameworks: ["MOEX ISS REST API", "MOEX TWIME / FIX Gateways", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing algorithmic trading strategies or execution algorithms connecting to the Moscow Exchange (MOEX / MICEX). MOEX operates distinct trading boards for Equities (`TQBR`), Foreign Exchange (`CETS`), and Derivatives (`FORTS`). Market data is retrieved via the MOEX ISS (Informational & Statistical Server) REST API, while low-latency order entry uses TWIME (binary protocol) or FIX protocol. This module manages board compatibility checks, tick step rounding, dynamic price collaring ($\pm 5.0\%$), and FIX/TWIME payload formatting.

## Prerequisites

- MOEX account configuration (`secid`, `board`: `'TQBR'`, `'CETS'`, `'RFUD'`, `account_id`, `client_code`, `max_price_collar_pct`: e.g. 0.05).
- Order request payload (`secid`, `board`, `side`: `'BUY'`/`'SELL'`, `quantity`, `price`, `reference_price`, `tick_size`: e.g. 0.01).

## Workflow

1. **Board & Security Identification**:
   - Verify `secid` matches board type (`TQBR` Equities, `CETS` FX, `RFUD` FORTS Derivatives).
2. **Tick Size & Price Collar Audit**:
   - Round order price to valid tick step ($P_{\text{rounded}} = \text{round}(P / \text{tick}) \times \text{tick}$).
   - Audit order price deviation against reference price:
     $$\Delta P_{\text{pct}} = \frac{|P_{\text{order}} - P_{\text{ref}}|}{P_{\text{ref}}}$$
   - If $\Delta P_{\text{pct}} > \text{collar\_limit} \implies$ Reject order (`MOEX_PRICE_COLLAR_BREACH`).
3. **FIX / TWIME Order Serialization**:
   - Generate MOEX compliant FIX/TWIME payload (`SecurityID`, `BoardID`, `Account`, `ClOrdID`, `Price`, `OrderQty`, `SecurityExchange='MISX'`).
4. **Audit Report Generation**: Output structured `MOEXOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Mixing Trading Boards**: Routing equity orders to `CETS` or FX pairs to `TQBR`, triggering immediate exchange FIX session rejects.
- **Ignoring RUB Tick Sizes**: Submitting prices with invalid tick increments (e.g. 0.005 on a 0.01 tick instrument), causing gateway validation failures.
- **Breaching Price Collars**: Submitting aggressive market orders during volatility spikes exceeding MOEX's dynamic price limits ($\pm 5.0\%$).

## Verification

- Instantiate `MOEXApiIntegrationEngine`. Audit SBER stock order on `TQBR` ($P = 280.50, P_{\text{ref}} = 280.00$, tick $= 0.01$) $\implies$ verify $0.18\%$ deviation passes price collar and generates FIX payload (`BoardID='TQBR'`, `SecurityExchange='MISX'`). Audit order breaching collar ($P = 310.00$ vs $P_{\text{ref}} = 280.00 \implies 10.7\% > 5.0\%$) $\implies$ verify `MOEX_PRICE_COLLAR_BREACH`.
- Run `python scripts/test_moscow_exchange_moex_api_integration.py`.

## Related Skills

- `interactive-brokers-global-multi-exchange-routing`
- `lse-millennium-exchange-api`
---
