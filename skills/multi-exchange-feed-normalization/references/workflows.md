# Deep Workflow Reference — multi-exchange-feed-normalization

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Define Canonical Internal Schema (`UnifiedTick`):**
   - Define fields: `symbol`, `venue`, `price`, `quantity`, `side`, `exchange_timestamp`, `receipt_timestamp`.

2. **Register Symbol Mappings:**
   - Map venue tickers (`BTCUSDT`, `BTC-USD`) to canonical symbols (`BTC/USD`) via `register_symbol_mapping()`.

3. **Implement Venue Payload Parsers:**
   - Extract raw prices, quantities, sides, and timestamps.
   - Coerce millisecond timestamps or ISO strings into float epoch seconds.

4. **Dispatch via Normalizer Engine:**
   - Pass raw payload to `TickNormalizerRegistry.normalize(venue, raw_payload)`.

## Failure Modes Observed in Production

- **Leaky Exchange Payload Attributes:** Allowing raw exchange fields (`s`, `p`, `q`) to leak into strategy feature calculations.
- **Timestamp Unit Mismatches:** Mixing millisecond timestamps from one venue with second timestamps from another venue.

## Production Implementation Reference

- Reference code: `scripts/feed_normalizer.py` (`TickNormalizerRegistry`, `UnifiedTick`, `NormalizedSide`).
- Automated unit tests: `scripts/test_feed_normalizer.py`.
