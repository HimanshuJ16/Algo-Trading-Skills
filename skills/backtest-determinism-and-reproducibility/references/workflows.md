# Deep Workflow Reference — backtest-determinism-and-reproducibility

This file holds the full technical procedure referenced by `SKILL.md`.
Sources for every factual claim are in `references/standards.md`.

## Full Procedure

### 1. Set `PYTHONHASHSEED` outside the process

```bash
PYTHONHASHSEED=0 python backtest.py
```

This must happen **before** the interpreter starts. Setting it from running code
is a no-op that looks like it worked:

```python
os.environ["PYTHONHASHSEED"] = "0"     # succeeds, reads back "0", changes nothing
```

`BacktestDeterminismEngine.check_hash_seed(seed)` returns whether the current
process has reproducible str/bytes hashing and warns if not. It deliberately
does not attempt to set the variable.

### 2. Seed the RNGs

```python
engine = BacktestDeterminismEngine(master_seed=42)   # seeds on construction
```

`apply_master_seeds()` covers `random`, NumPy's legacy global `RandomState`, and
`torch` (plus CUDA) when installed.

**What it does not cover:** modern NumPy. `np.random.default_rng()` draws fresh
OS entropy and is unaffected by `np.random.seed()`. Thread an explicit generator
through instead:

```python
rng = engine.make_numpy_generator()   # np.random.default_rng(master_seed)
slippage = rng.normal(0.0, 0.0001, size=n)
```

Pass `apply_seeds_on_init=False` to build an audit-only engine that does not
mutate process-global RNG state.

### 3. Sort the event stream

```python
events = BacktestDeterminismEngine.sort_event_stream(raw_events)
```

Sorts on `(timestamp, symbol.upper(), sequence_id)`. Two rules that differ from
a naive `sorted()`:

- **All three keys are required.** A missing `timestamp` raises rather than
  defaulting to `0.0`, which would silently relocate the event to the front of
  the stream.
- **Duplicate sort keys are rejected.** Python's sort is stable, so tied events
  retain their input order — meaning the replay would depend on the order rows
  came out of the file. Assign distinct `sequence_id` values.

### 4. Use a simulated clock

```python
clock = SimulatedClock(start_time=events[0]["timestamp"])
for event in events:
    clock.advance_to(event["timestamp"])
    strategy.on_event(event, now=clock.now())
```

Inject the clock rather than monkey-patching `time.time()`. Patching a global
affects every library in the process, including any whose correctness depends on
real elapsed time (timeouts, retry backoff, connection keepalives).

`advance_to()` raises on a backwards jump, which turns an unsorted stream into an
immediate, located error instead of a subtly wrong result.

### 5. Checksum and compare

```python
report = engine.audit_reproducibility(trades_run1, trades_run2)
if not report.is_bit_identical:
    raise AssertionError(
        f"non-deterministic backtest; first divergence at trade "
        f"{report.first_divergence_index}: {report.message}"
    )
```

Floats are hashed via `float.hex()`, preserving every bit. `first_divergence_index`
points at the first differing trade so a CI failure is actionable.

Non-finite values are rejected at record construction: `json.dumps` renders every
NaN as the same token, so two runs that both corrupted to NaN would otherwise
hash identically and be reported as reproducible.

## Worked Example

```python
from reproducibility_engine import (
    BacktestDeterminismEngine, SimulatedClock, TradeExecutionRecord,
)

engine = BacktestDeterminismEngine(master_seed=42)

trades = [
    TradeExecutionRecord(1000.0, "AAPL", "BUY", 100.0, 150.0),
    TradeExecutionRecord(1005.0, "AAPL", "SELL", 100.0, 155.0),
]

report = engine.audit_reproducibility(trades, list(trades))
report.is_bit_identical   # True
report.net_cash_flow      # 500.0  -- signed: -100*150 + 100*155
report.total_trades       # 2
```

`net_cash_flow` carries the side's sign. It equals realized P&L only when the
position is flat; with an open position it is the cash effect so far. The
pre-2.0 field `final_equity` summed unsigned notional and reported `30500.0`
for this same round trip.

## CI Integration

Run the backtest twice in the same job and fail the build on divergence:

```python
report = engine.audit_reproducibility(run_backtest(), run_backtest())
assert report.is_bit_identical, report.message
```

Pin the environment for any comparison you intend to trust — Python version,
NumPy/BLAS versions, and thread counts (`OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`). Comparing across environments will produce legitimate
divergence that no checksum can reconcile.

## Production Implementation Reference

- Reference code: `scripts/reproducibility_engine.py`
  (`BacktestDeterminismEngine`, `TradeExecutionRecord`, `SimulatedClock`,
  `DeterministicAuditReport`, `DeterminismError`).
- Automated unit tests: `scripts/test_reproducibility_engine.py`.
- Sources and limits: `references/standards.md`.

## Failure Modes Seen in This Skill's Own History

- **A detector blind to what it detects** — rounding floats to 6 decimals before
  hashing, so summation-order divergence read as bit-identical.
- **NaN laundering** — two NaN-corrupted runs hashing to the same value and
  being declared reproducible.
- **Unsigned notional labelled `final_equity`** — 30,500 reported for a round
  trip that made 500.
- **Silent sort defaults** — a missing timestamp defaulting to `0.0` and
  reordering the stream.
- **Documented-but-absent clock** — the workflow described replacing
  `time.time()` while the engine implemented no clock at all.
