# Deep Workflow Reference — multi-exchange-feed-normalization

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Choose the registry's safety policies explicitly

```python
registry = TickNormalizerRegistry(
    strict_symbols=True,                       # unmapped ticker -> raise
    naive_timestamp_tz=datetime.timezone.utc,  # None => host local (pykiteconnect)
    allow_non_positive_price=False,            # True only for negative-price instruments
)
```

Each default is the conservative choice. Relaxing one is a decision with a downstream
consequence, documented in `SKILL.md` under *Common Pitfalls* — not a convenience knob.

### 2. Register symbol mappings before the feed starts

```python
registry.register_symbol_mapping("binance", "BTCUSDT", "BTC/USD")
registry.register_symbol_mapping("coinbase", "BTC-USD", "BTC/USD")
```

Mappings are keyed on `(venue.lower(), raw_symbol.upper())`. With `strict_symbols=True`
an unregistered ticker raises at parse time, which is the intended behaviour: a missing
mapping is a configuration error, and the alternative — passing the raw ticker through —
creates a phantom second instrument carrying part of the volume.

### 3. Capture arrival time at the socket, parse later

```python
raw = await ws.recv()
arrival = time.time()          # <- here, not after the queue
await queue.put((raw, arrival))
...
payload, arrival = await queue.get()
tick = registry.normalize("binance", json.loads(payload), receipt_timestamp=arrival)
```

`receipt_timestamp` defaults to parse time. That is correct only when parsing happens
inline on the read path. With a queue in between, the default measures dequeue time and
folds queue depth into every latency number derived from the tick — precisely when the
system is under load and the measurement matters most.

### 4. Add venues by registering a parser

```python
def parse_kraken(payload, receipt_timestamp=None):
    return UnifiedTick(
        symbol=registry.get_canonical_symbol("kraken", payload["pair"]),
        venue="kraken",
        price=float(payload["price"]),
        quantity=float(payload["volume"]),
        side=NormalizedSide.BUY if payload["side"] == "b" else NormalizedSide.SELL,
        exchange_timestamp=registry._coerce_timestamp(payload["time"], "time"),
        receipt_timestamp=receipt_timestamp or time.time(),
    )

registry.register_parser("kraken", parse_kraken, TimestampUnit.SECONDS)
```

Before writing the `side` line, read the venue's own documentation for what its side
field denotes. Confirm whether it is the maker or the taker; the two are not
distinguishable by inspection of the data. `UnifiedTick.__post_init__` enforces the
schema invariants regardless of which parser produced the tick, so a custom parser
cannot emit a NaN price or a millisecond value in the seconds field.

### 5. Consume `UnifiedTick` and handle `UNKNOWN`

```python
if tick.side is NormalizedSide.UNKNOWN:
    continue          # excluded from the signed sum, not coerced to a side
imbalance += tick.quantity if tick.side is NormalizedSide.BUY else -tick.quantity
```

Every Zerodha tick is `UNKNOWN`. Coercing those to `BUY` manufactures a permanent
buying bias in the imbalance series.

### 6. Dead-letter rejected payloads; alert on the rejection rate

```python
try:
    tick = registry.normalize(venue, payload, receipt_timestamp=arrival)
except NormalizationError:
    logger.exception("Rejected %s payload", venue)
    dead_letter.put(payload)
    rejected_counter.labels(venue).inc()
    continue
```

Do not retry: rejection means the message was unreadable, and re-parsing identical bytes
produces an identical rejection. A rising rejection rate is the signal this design
exists to produce — usually an undocumented venue schema change.

## Known Failure Modes

- **Cross-venue side inversion.** Copying Coinbase's `side` through while deriving
  Binance's from `m` places the two venues on opposite conventions. Each venue looks
  self-consistent under single-venue tests; only a cross-venue assertion catches it.
  See `references/standards.md` for the derivation table.

- **Cumulative volume read as trade size.** Falling back from Zerodha's
  `last_traded_quantity` to `volume_traded` (or REST `last_quantity` to `volume`)
  reports the session's running total as one print. This does not degrade gracefully —
  it inflates volume-weighted statistics by orders of magnitude.

- **Timestamp unit guessed from magnitude.** The `> 1e11 -> divide by 1000` rule sends
  Binance's documented `timeUnit=MICROSECOND` streams to roughly the year 55839 without
  raising. Test disjoint per-unit bands and reject anything matching none of them.

- **Fabricated timestamps on parse failure.** Substituting `time.time()` makes
  `receipt - exchange` collapse to ~0, so a stale feed reports perfect latency and every
  staleness monitor goes blind. The same applies to `price=0.0` for a missing price and
  `side=BUY` for a missing flag: each converts a loud failure into a silent corruption.

- **Naive timestamps assumed to be UTC.** `pykiteconnect` produces naive datetimes in
  the recording host's local zone; assuming UTC shifts every Indian tick by 5h30m on a
  UTC-clocked server.

- **Leaky exchange payload attributes.** Any strategy code reading `s`, `p`, `q`, or
  `last_price` means the boundary is not a boundary and per-venue branching has moved
  downstream rather than disappeared.

## Production Implementation Reference

- Reference code: `scripts/feed_normalizer.py` — `TickNormalizerRegistry`,
  `UnifiedTick`, `NormalizedSide`, `TimestampUnit`, `NormalizationError`.
- Automated unit tests: `scripts/test_feed_normalizer.py`. Tests marked `REGRESSION`
  each fail against the pre-2.0 implementation and pass against the current one.
- Venue field tables and source citations: `references/standards.md`.
