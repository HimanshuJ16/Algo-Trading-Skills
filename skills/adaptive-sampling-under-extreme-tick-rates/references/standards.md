# Standards for Adaptive Sampling Under Extreme Tick Rates

## Sampling Policy

The sampler protects a bounded downstream processing budget; it does not change the source feed or establish a market-data entitlement. Calibrate `target_max_rate_per_sec` from measured capacity and latency objectives for each deployment.

| Condition | Mode | Factor | Output contract |
|---|---|---:|---|
| `rate <= target` | `PASSTHROUGH` | `1` | Emit each valid trade as a one-tick aggregate. |
| `rate > target` | `SYSTEMATIC_SAMPLING` | `ceil(rate / target)`, minimum `2` | Emit one aggregate after the configured number of input trades. |
| Stream end/checkpoint | Synthetic flush | `1` | Emit residual volume with `is_flush=True` and a deterministic event timestamp. |

The rate estimate is an approximate one-second event-time estimate using the current and immediately previous second. It is a control signal, not a measurement of venue liquidity or a substitute for feed-health monitoring.

## Accounting Invariants

For every symbol and completed lifecycle interval:

- `sum(emitted volume) == sum(valid input volume)` within the configured numeric tolerance.
- `sum(emitted price * emitted volume) == sum(input price * input volume)` within the configured numeric tolerance.
- `emitted price = aggregate notional / aggregate volume` for every aggregate with positive volume.
- Every emitted sample reports `aggregated_tick_count >= 1`; synthetic residuals report `is_flush=True` and `sequence_id=-1`.
- A rejected tick changes no sampler state.
- A duplicate or decreasing sequence is rejected when `enforce_monotonic_sequence=True`.
- Event timestamps must be finite and non-decreasing per symbol; an explicit flush timestamp cannot precede the last accepted event.

Floating-point reconciliation must use a documented tolerance. If the feed requires exact decimal accounting, replace the numeric accumulator with a decimal/fixed-point representation at the integration boundary.

## Feed-Integrity Boundaries

The sampler assumes the upstream feed has already handled authentication, entitlement, transport integrity, sequence-gap recovery, duplicate detection policy, and venue restart semantics. A sampler cannot reconstruct missing trades or infer order-book state from sampled trades.

Treat these as separate signals and operational states:

- **Malformed input**: reject and alert; do not retry as a new trade.
- **Sequence gap or feed reset**: pause or isolate the symbol, reconcile upstream, flush accepted residual state, then call `reset_symbol` before restarting the sequence domain.
- **Stale event time**: reject or quarantine according to the feed policy; never move the rolling window backwards.
- **Downstream overload**: use backpressure or a documented degrade policy in addition to sampling; sampling alone does not guarantee bounded memory.

## Operational Controls

- Use one engine per independent sequence domain unless the feed contract guarantees a shared monotonic sequence per symbol.
- Keep the engine lock scope limited to validation and in-memory state updates; perform persistence and downstream processing outside the lock.
- Call `flush_all()` on shutdown and checkpoint boundaries; record every flush and reconciliation result.
- Call `reset_symbol(flush=True)` when a symbol is retired or its sequence/timestamp domain restarts. Never delete state without deciding what happens to residual volume.
- Monitor input/output rate, sampling factor, aggregate count, validation failures, residual volume, volume/notional reconciliation, queue depth, and processing latency.

## Related Standards

- Exchange and vendor feed protocols commonly expose sequence identifiers, event timestamps, and recovery semantics; bind the engine's validation policy to the exact protocol used by the integration.
- Market-access and trading-risk controls remain outside this analytics/throughput component. Sampled data must not weaken independent pre-trade, market-status, price-band, or regulatory controls.
- Review the applicable venue/vendor protocol, data-license terms, and retention obligations before deploying sampling in a production trading path.