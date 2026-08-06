---
name: vix-and-volatility-index-derivative-strategies
description: "Institutional volatility trading skill for analyzing VIX futures term structure (Contango vs Backwardation), calculating roll yield decay, executing VIX Roll Yield Harvesting (Short VIX Futures) and Tail Risk Protection (VIX Call Spreads), and pricing options off forward VIX futures curves."
domain: Multi-Asset Derivatives & Volatility Trading
subdomain: VIX Futures & Options Strategies
tags:
- vix
- volatility-derivatives
- vix-futures
- term-structure
- contango
- backwardation
- roll-yield
- tail-hedging
brokers_frameworks:
- cboe
- vix-futures
- vix-options
- svxy
- uvxy
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when trading VIX futures ($VX$), VIX options, volatility ETPs (SVXY, UVXY, VXX), or designing quantitative volatility tail hedges for equity portfolios.

This skill provides institutional mechanisms to:
- Classify VIX Futures Term Structure into `CONTANGO` ($F_1 < F_2$), `BACKWARDATION` ($F_1 > F_2$), or `FLAT`.
- Calculate **Annualized Roll Yield** ($\text{Roll Yield \%} = \frac{F_1 - S_{\text{VIX}}}{S_{\text{VIX}}} \times \frac{365}{D_{\text{expiry}}}$) and daily dollar decay.
- Execute **VIX Roll Yield Harvesting** (Short $F_1$ Futures / Long Inverse Volatility) during steep contango.
- Structure **VIX Tail Risk Protection** (Long Out-of-the-Money VIX Call Spreads priced off $F_1$ forward futures) during backwardation or market sell-offs.
- Size derivative contracts based on portfolio equity and risk tolerance limits.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- Real-time or historical spot VIX ($S_{\text{VIX}}$) and front-two VIX futures quotes ($F_1, F_2$).

## Workflow

1. **Construct VIX Futures Inputs**: Define `VIXFuturesContract` instances for front-month ($F_1$) and second-month ($F_2$) futures with expiry dates, days to expiry, and settlement prices.
2. **Analyze Term Structure**: Call `analyze_term_structure(spot_vix, f1, f2)` to determine slope ($F_2 - F_1$), term structure state (`CONTANGO`, `BACKWARDATION`), and annualized roll yield %.
3. **Generate Tactical Signal**: Invoke `generate_strategy_signal(term_struct, portfolio_equity_usd)` to get position recommendations (`SHORT_F1_VIX_FUTURE`, `LONG_VIX_CALL_SPREAD`, `CASH`) and contract counts.
4. **Price VIX Call Spreads**: Call `price_vix_call_spread(f1_price, strike_lower, strike_upper, days_to_expiry)` to calculate max profit and max loss per contract ($1,000 multiplier).
5. **Monitor & Rebalance**: Rebalance positions as futures approach monthly settlement or term structure flips.

## Common Pitfalls

- **Priced Off Spot VIX Fallacy**: VIX options and futures are priced off the expected future VIX at expiry ($F_1$), NOT current spot VIX ($S_{\text{VIX}}$). Buying VIX calls when spot VIX spikes without checking $F_1$ leads to mispriced entries.
- **Short Volatility Blowout Risk (Volageddon)**: Shorting VIX futures during contango yields steady roll decay but carries catastrophic tail risk during volatility spikes (e.g. Feb 2018 XIV collapse). Hard stop-loss limits are mandatory.
- **Neglecting the $1,000 Contract Multiplier**: Cboe VIX Futures and Options use a **$1,000 contract multiplier** per index point ($1.0\ \text{VIX point} = \$1,000$). Position sizing must account for this multiplier.
- **Expiry Settlement Misalignment**: VIX futures settle on Wednesdays based on a special opening quotation (SOQ) of SPX options. Holding futures into the morning of expiration exposes positions to settlement auction gap risk.

## Verification

Run the unit test suite to validate contango/backwardation classification, roll yield calculations, tail hedge signal generation, and call spread pricing:

```bash
python -m unittest discover -s skills/vix-and-volatility-index-derivative-strategies/scripts
```

## Related Skills

- `variance-swap-and-volatility-derivative-pricing`
- `total-return-swap-synthetic-exposure`
- `warrants-and-structured-product-integration`
- `tick-size-pilot-program-impact-assessment`

