---
name: multi-exchange-feed-normalization
description: Use when building multi-exchange market data pipelines to map heterogeneous
  WebSocket/REST tick payloads into a single unified internal schema with normalized
  symbols, prices, quantities, and timestamps
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- feed-normalization
- multi-exchange
- unified-tick
- market-data-schema
brokers_frameworks:
- Binance
- Coinbase
- Zerodha
- IBKR
- Kraken
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a quantitative trading system ingests market data from multiple exchanges or brokers simultaneously. Every exchange uses distinct JSON schemas, field names (`p` vs `price` vs `last_price`), timestamp formats (milliseconds vs ISO strings), side indicators (`b`/`a` vs `BUY`/`SELL`), and symbol naming conventions (`BTCUSDT` vs `BTC-USD` vs `NIFTY24JULFUT`). Normalizing raw venue payloads into a single canonical `UnifiedTick` data structure before routing to strategy engines or feature stores is mandatory to prevent per-venue code branching.

## Prerequisites

- Standardized `UnifiedTick` dataclass definition.
- Symbol mapping dictionary (mapping venue-specific tickers to unified canonical symbols).
- Venue-specific parser implementations (e.g. `BinanceParser`, `CoinbaseParser`, `ZerodhaParser`).

## Workflow

1. **Define Canonical Internal Schema (`UnifiedTick`)**:
   - Create standard data model containing `symbol`, `venue`, `price`, `quantity`, `side`, `exchange_timestamp`, and `receipt_timestamp`.

2. **Implement Venue Parser Interfaces**:
   - Implement dedicated parser functions for each exchange venue:
     - Binance: `p` $\rightarrow$ price, `q` $\rightarrow$ quantity, `T` $\rightarrow$ timestamp_ms, `m` $\rightarrow$ buyer_is_maker.
     - Coinbase: `price` $\rightarrow$ price, `size` $\rightarrow$ quantity, `time` $\rightarrow$ ISO 8601 timestamp.
     - Zerodha: `last_price` $\rightarrow$ price, `volume` $\rightarrow$ quantity, `last_trade_time` $\rightarrow$ timestamp.

3. **Symbol & Side Normalization**:
   - Translate venue symbols (`BTC-USD`, `BTCUSDT`) into canonical unified symbols (`BTC/USD`).
   - Translate trade side flags into normalized `OrderSide` enum (`BUY`, `SELL`, `UNKNOWN`).

4. **Timestamp Standardization**:
   - Coerce all exchange timestamps into Unix epoch float seconds ($1700000000.123$).

5. **Register Normalizer Engine**:
   - Pass incoming raw message streams to `TickNormalizerRegistry.normalize(venue, raw_payload)`.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Leaky Venue Schemas**: Allowing raw exchange-specific fields to spill past the normalization boundary into strategy calculations.
- **Inconsistent Timestamp Resolution**: Mixing millisecond timestamps from one exchange with second timestamps from another.
- **Symbol Mapping Collisions**: Failing to handle exchange-specific symbol suffixes (e.g. `.NS` for NSE India or `-PERP` for futures).

## Verification

- Submit raw Binance, Coinbase, and Zerodha tick payloads and verify all output `UnifiedTick` objects share identical field names and types.
- Verify timestamp coercion converts ISO strings and millisecond ints to standard float seconds.
- Verify symbol mapping converts `BTCUSDT` and `BTC-USD` to `BTC/USD`.
- Run unit test suite `python scripts/test_feed_normalizer.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `broker-agnostic-adapter-interface`
- `clock-skew-correction-for-tick-timestamps`
---
