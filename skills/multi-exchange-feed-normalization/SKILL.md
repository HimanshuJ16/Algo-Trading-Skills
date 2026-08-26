---
name: multi-exchange-feed-normalization
description: Use when a trading system ingests trade ticks from more than one venue,
  to map heterogeneous WebSocket/REST payloads onto one canonical UnifiedTick with a
  single symbol namespace, a single aggressor-side convention, and one timestamp
  timescale, failing loudly instead of substituting defaults for fields it cannot read
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
- Zerodha Kite
- Custom venues via register_parser
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this whenever a quantitative system consumes trade prints from more than one venue
and downstream code must not branch per venue. Exchanges disagree on field names (`p`
vs `price` vs `last_price`), timestamp units (millisecond ints, ISO-8601 strings, naive
`datetime` objects), symbol conventions (`BTCUSDT` vs `BTC-USD`), and — most
dangerously — on **what the trade's `side` field means**.

The normalization boundary is where those disagreements get resolved once. Everything
past it sees `UnifiedTick` and nothing else.

Two properties of this implementation are load-bearing, and both are the opposite of
the obvious approach:

- **`UnifiedTick.side` is always the aggressor (taker) side**, derived per venue rather
  than copied. See Common Pitfalls.
- **Nothing is defaulted.** Every unreadable field raises `NormalizationError`. A
  normalizer that substitutes `price=0.0`, `side=BUY`, or `timestamp=now()` converts a
  loud feed failure into a silent data corruption that no downstream consumer can
  detect.

## When NOT to Use

- **For quotes, book updates, or bar data.** `UnifiedTick` models a *trade print*:
  one price, one size, one aggressor. It has no bid/ask, no level, no sequence number.
  Use `order-book-depth-processing-l2-l3` for book state.
- **As a sequencing, dedup, or gap-detection layer.** This maps one payload to one tick
  and holds no cross-message state. Reconnects will re-deliver ticks and this module
  will happily normalize each copy. Gap detection belongs in
  `sequence-number-gap-detection-for-feeds`; cross-venue disagreement on the same
  instrument belongs in `multi-source-price-reconciliation-tie-breaking`.
- **As a clock-correction layer.** `exchange_timestamp` is whatever the venue stamped;
  it is not adjusted for the venue's clock offset relative to yours. Do not difference
  timestamps across venues to compare event ordering without first correcting skew —
  see `clock-skew-correction-for-tick-timestamps`.
- **On Coinbase data that a vendor has already converted to taker convention.**
  `parse_coinbase` inverts `side` because Coinbase publishes the maker side. If your
  aggregator already flipped it, the two inversions cancel and every Coinbase side is
  wrong. Verify which convention your source uses before wiring it up.
- **As an entitlement or licensing boundary.** Normalizing a vendor's data does not
  make it redistributable — see `market-data-entitlement-and-licensing-per-venue`.

## Prerequisites

- A decoded venue payload as a `Mapping` (this module does not do transport, framing,
  or JSON decoding).
- A registered symbol mapping per (venue, ticker) pair. Required by default:
  `strict_symbols=True` rejects unmapped tickers rather than inventing a symbol.
- The arrival timestamp captured **at socket read**, if you queue payloads before
  parsing.
- A decision on the timezone of naive timestamps (`naive_timestamp_tz`). Defaults to
  UTC; `pykiteconnect` consumers want `None` (host local).

## Workflow

1. **Instantiate the registry with its safety policies chosen deliberately.**
   `strict_symbols`, `naive_timestamp_tz`, and `allow_non_positive_price` each default
   to the conservative setting. Relax one only with a stated reason — the defaults are
   what stop a malformed feed from looking healthy.

2. **Register every (venue, ticker) → canonical symbol pair before the feed starts.**
   `register_symbol_mapping("binance", "BTCUSDT", "BTC/USD")`. If a symbol is missing at
   runtime, the tick raises rather than passing through: an unmapped ticker that flows
   downstream becomes a *second, phantom instrument* holding half the volume, and that
   stays invisible until a position report disagrees with the broker.

3. **Capture the receipt timestamp where the bytes arrive, not where you parse.**
   `normalize(venue, payload, receipt_timestamp=arrival)`. If you omit it, the tick is
   stamped at parse time; when a queue sits between the socket and the parser, every
   latency figure computed from that tick silently includes the queue depth.

4. **Dispatch through `normalize()`; add venues with `register_parser()`.** The three
   built-in parsers are ordinary registered callables, not a hard-coded branch. A new
   venue is a function of `(payload, receipt_timestamp) -> UnifiedTick`, not a fork of
   this module. Declare `timestamp_unit` if the venue's epoch scale is known — leave it
   `AUTO` to have it inferred from magnitude bands.

5. **Resolve the aggressor side per venue, never by copying the venue's field.**
   Binance's `m` ("is the buyer the market maker?") must be inverted; Coinbase's `side`
   is the *maker* side and must also be inverted; Zerodha carries no aggressor
   information at all and yields `UNKNOWN`. Confirm the convention in the venue's own
   documentation before adding a parser.

6. **Handle `UNKNOWN` explicitly at the consumer.** It is a real, expected value, not a
   parse failure. Order-flow imbalance must exclude those ticks from the signed sum
   rather than treat them as either side — every Zerodha tick is `UNKNOWN`, so silently
   coercing them to `BUY` would manufacture a permanent buying bias.

7. **Catch `NormalizationError` at the venue boundary and dead-letter the payload.**
   Do not retry a rejected payload: rejection means the message was unreadable, and
   re-parsing identical bytes yields an identical rejection. Alert on the rate — a
   sudden rise in rejections is usually a venue schema change, which is the failure this
   design is built to make visible.

> Full step-by-step procedure with venue-specific detail: see `references/workflows.md`.
> Venue field mapping table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Copying the venue's `side` field straight through.** This is the defect most likely
  to survive testing, because each venue looks self-consistent in isolation. Binance
  documents `m` as "Is the buyer the market maker?", so `m=true` means the aggressor was
  the **seller**. Coinbase documents `side` on both the Exchange `matches` channel and
  the Advanced Trade `market_trades` channel as the **maker** order side. Derive
  Binance's side from `m` while passing Coinbase's through unchanged and the two venues
  end up on opposite conventions — cross-venue order-flow imbalance is then sign-flipped
  for one of them, with no error anywhere.

- **Reading Zerodha's cumulative volume as a trade size.** `pykiteconnect`'s WebSocket
  ticker emits `last_traded_quantity` (trade size) and `volume_traded` (cumulative
  session volume); the REST quote endpoint names the same two fields `last_quantity` and
  `volume`. Falling back from the trade size to the volume field does not degrade
  gracefully — it reports the day's running total as a single print, inflating every
  volume-weighted statistic by orders of magnitude.

- **Guessing a timestamp's unit from its magnitude.** The common `if ts > 1e11: ts /=
  1000` rule breaks on the same Binance streams served with `timeUnit=MICROSECOND`,
  yielding timestamps around the year 55839 with no error raised. Infer against
  *disjoint* per-unit bands (they sit three decades apart) or declare the unit per
  venue; reject anything matching no band.

- **Treating naive timestamps as UTC by default.** `pykiteconnect` builds
  `last_trade_time` with `datetime.fromtimestamp(epoch)`, which is naive *in the
  recording host's local zone*. Calling `.timestamp()` on it round-trips correctly only
  on that host; assuming UTC on a UTC-clocked server shifts every Indian tick by 5h30m.
  Make the assumed zone an explicit setting.

- **Substituting `time.time()` when a timestamp will not parse.** This is the worst
  available default. It makes `receipt_timestamp - exchange_timestamp` collapse to
  roughly zero, so a stale or corrupt feed reports *perfect* latency and every staleness
  and clock-skew monitor downstream goes blind at exactly the moment it was needed.

- **Letting an unmapped symbol pass through verbatim.** `BTCUSDT` and `BTC-USD` then
  coexist as two unrelated instruments, splitting the consolidated view of one tradable.

- **Leaking raw venue fields past the boundary.** If any strategy code reads `s`, `p`,
  or `last_price`, the normalization layer is not actually a boundary and per-venue
  branching has merely moved downstream.

## Verification

- Normalize Binance, Coinbase, and Zerodha payloads and confirm every output is a
  `UnifiedTick` with identical field names and types (`test_all_venues_share_one_schema`).
- Confirm the **cross-venue side convention**: a resting bid being hit is `SELL` on both
  venues — Binance `m=true` and Coinbase maker `side="buy"` must produce the same
  `NormalizedSide` (`test_economically_identical_trades_agree_across_venues`).
- Confirm seconds, milliseconds, microseconds and nanosecond epochs all resolve to the
  same instant, and that an out-of-band value raises.
- Confirm a missing price, a `nan` price, a zero quantity, a missing timestamp, and an
  unmapped symbol each raise `NormalizationError` rather than producing a tick.
- Confirm a naive Kite `datetime` is interpreted in the configured zone, not silently
  replaced with the current time.
- Run `python -m unittest discover -s skills/multi-exchange-feed-normalization/scripts`
  and confirm all tests pass.

## Related Skills

- `producer-consumer-tick-pipeline`
- `broker-agnostic-adapter-interface`
- `clock-skew-correction-for-tick-timestamps`
- `sequence-number-gap-detection-for-feeds`
- `reference-data-symbol-mapping-across-vendors`
- `tick-data-schema-versioning`
