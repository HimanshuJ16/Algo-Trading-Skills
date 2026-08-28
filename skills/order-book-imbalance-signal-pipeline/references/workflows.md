# Deep Workflow Reference — order-book-imbalance-signal-pipeline

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Configure once, before the stream starts

```python
from imbalance_pipeline import FastPathOBIPipelineEngine

engine = FastPathOBIPipelineEngine(
    imbalance_threshold=0.60,   # calibrate per instrument; (0.0, 1.0]
    depth_levels=1,             # 1 = top of book
    signal_callback=strategy.on_obi_signal,
    strict=False,               # True: raise on the first untrustworthy update
    allow_locked_book=False,    # True only where locked markets are legal
)
```

`OBIConfigurationError` is raised immediately for a threshold outside $(0, 1]$
(including `NaN`, `inf` and `True`) or a `depth_levels` below 1. A threshold of
`0.0` would classify every balanced book as `HIGH_BUY_PRESSURE`, since $I \ge 0$
is satisfied by $I = 0$.

Pick `strict` deliberately. `strict=True` raises `OBIValidationError` on the
first bad update, which is right for a validated production feed where a bad tick
is an incident. The default records and continues, which is right for replaying
an imperfect archive.

### 2. Feed one book update at a time

```python
from imbalance_pipeline import L2OrderBookTop

update = L2OrderBookTop(
    symbol="AAPL",
    bid_price=189.98, bid_volume=800.0,
    ask_price=189.99, ask_volume=200.0,
    timestamp_ns=1_690_000_000_123_456_789,
    bid_depth=[(189.97, 400.0)],   # levels BEHIND the touch, outward
    ask_depth=[(190.00, 600.0)],   # omit entirely when depth_levels == 1
)
result = engine.process_l2_update(update)
```

`bid_depth` / `ask_depth` must **exclude** level 1. Prices must move strictly
away from the touch — descending for bids, ascending for asks. A ladder whose
first entry equals the touch price is the signature of a caller passing the whole
book, which would count the best queue twice; it is rejected as
`MALFORMED_DEPTH`.

### 3. Validation order, and why it is that order

1. **Volumes**: finite, numeric, $\ge 0$. `NaN` first, because `NaN` survives
   every subsequent comparison — `total <= 0.0` is `False` for `NaN`, so an
   unguarded path computes a `NaN` imbalance and a `NaN` weighted mid and then
   classifies them `NEUTRAL`, since `NaN >= threshold` and `NaN <= -threshold`
   are both `False`. Negative volumes are rejected here too: they are finite, so
   nothing downstream catches them, and they drive $|I|$ outside $[-1, +1]$
   (a bid of $-100$ against an ask of $200$ yields $I = -3.0$).
2. **Prices**: finite and strictly positive. A zero price yields $W = 0$ and
   $M = 0$ — numbers an execution worker will happily use.
3. **Book geometry**: $P_{\text{bid}} > P_{\text{ask}}$ is always rejected;
   $P_{\text{bid}} = P_{\text{ask}}$ is rejected unless `allow_locked_book`.
4. **Timestamp type**: a non-negative `int`. A float `NaN` would pass the
   ordering comparison in step 5, be stored, and then disable that check for the
   following update as well.
5. **Ordering**: `timestamp_ns` below the last accepted value *for that symbol*.
   Symbols are tracked independently, so interleaving instruments on one feed is
   safe. A rejected update does not advance the clock, so a transient bad tick
   cannot lock out the updates that follow it.
6. **Depth ladders**: a concrete `list`/`tuple` (a generator is consumed by the
   scan and then fails the length check), correct shape, monotonic prices, and
   per-level price/volume validity.
7. **Depth sufficiency**: exactly `depth_levels` levels per side must be
   available. Falling back to a shallower window would silently redefine the
   signal mid-stream.
8. **Aggregate volume** $> 0$.

### 4. Computation

$$I = \frac{\sum_{i=1}^{N} V_{\text{bid},i} - \sum_{i=1}^{N} V_{\text{ask},i}}{\sum_{i=1}^{N} V_{\text{bid},i} + \sum_{i=1}^{N} V_{\text{ask},i}}, \qquad N = \texttt{depth\_levels}$$

Level sums use `math.fsum`, which is exact for the addition, so aggregating many
levels of widely differing size does not accumulate error.

$$W = \frac{V_{\text{bid},1} P_{\text{ask},1} + V_{\text{ask},1} P_{\text{bid},1}}{V_{\text{bid},1} + V_{\text{ask},1}}$$

$W$ is **always top-of-book**, even when $I$ aggregates depth: the weighted mid
is defined on the touch prices, and weighting deeper prices by deeper sizes
produces a quantity with no standard interpretation. When both touch queues are
empty but depth behind them is not, $I$ exists and $W$ is `None`.

Invariant, useful for testing and for understanding what $W$ adds:

$$W - M = \frac{I_{\text{top}} \cdot s}{2}$$

### 5. Classification

- $I \ge$ threshold → `HIGH_BUY_PRESSURE`
- $I \le -$threshold → `HIGH_SELL_PRESSURE`
- otherwise → `NEUTRAL`

Classification uses the exact float that the result reports. The previous
implementation reported `round(I, 4)` while classifying the unrounded value, so a
book with $I = 0.59998$ produced `imbalance = 0.6` alongside `NEUTRAL` — a
contradiction visible to anyone reconciling the log against the classification.

`UNRELIABLE` is a fourth state, never produced by the classifier: it means the
update never reached it.

### 6. Dispatch

Only `HIGH_BUY_PRESSURE` and `HIGH_SELL_PRESSURE` reach `signal_callback`
(`result.is_actionable`). The callback runs inside a `try/except Exception`:
failures increment `callback_error_count` and log with a traceback, and the feed
loop continues. `BaseException` is not caught, so `KeyboardInterrupt` and
interpreter shutdown behave normally.

Keep the callback non-blocking. A database write, JSON serialisation or a
synchronous REST call there inserts its own latency between every tick and the
next; hand off to a queue — see `producer-consumer-tick-pipeline`.

### 7. Reporting

```python
report = engine.generate_report()
assert report.status == "OBI_PIPELINE_CLEAN"
```

`OBI_PIPELINE_CLEAN` requires zero rejections and zero callback errors.
Otherwise `OBI_PIPELINE_DEGRADED` carries a per-kind breakdown, and any imbalance
statistic aggregated over that run was computed on a partial sample.

## Interpreting `calculation_latency_ns`

The field times validation, arithmetic and classification. It **excludes** result
construction, logging and callback dispatch, and it is floor-limited by the
platform clock — `time.perf_counter_ns()` resolves to 100 ns on the reference
platform, so small readings are a handful of clock ticks, not a precise figure.

Reference measurements (CPython 3.11, Windows 11, commodity laptop, warm loop,
best of three runs of 200k iterations):

| Version | End-to-end per call |
|---|---|
| This engine, full validation | ~1.9 µs |
| Same arithmetic, no validation | ~1.4 µs |

Roughly 0.4 µs buys the fail-closed contract. Neither figure is sub-microsecond;
CPython does not get there. Use `calculation_latency_ns` as a relative regression
tripwire only, and measure the real path with
`tick-to-trade-latency-measurement`.

## Production Implementation Reference

- Reference code: `scripts/imbalance_pipeline.py`
  (`FastPathOBIPipelineEngine`, `L2OrderBookTop`, `OBISignalResult`,
  `OBIPipelineReport`, `ImbalanceSignalType`, `OBIConfigurationError`,
  `OBIValidationError`).
- Automated unit tests: `scripts/test_imbalance_pipeline.py` (61 tests).
