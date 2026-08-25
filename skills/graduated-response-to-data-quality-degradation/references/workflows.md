# Workflows — Graduated Response to Data Quality Degradation

## 1. Metric ingestion

Emit one `DataQualityMetrics` per symbol per evaluation window from the feed-quality
collector:

| Field | Meaning | If you do not measure it |
|---|---|---|
| `stale_time_seconds` | Age of the most recent *accepted* tick, in seconds. | No default exists. You must measure it. |
| `missing_sequence_count` | Sequence numbers detected missing in the window. | Stays 0 and the penalty never fires. Feeds without sequence numbers legitimately report 0. |
| `price_spike_anomaly_detected` | Upstream spike classifier verdict. | Stays `False`. |
| `crossed_book_detected` | Bid strictly above ask, same instrument, same instant. | Stays `False`. |
| `bid_ask_spread_multiplier` | Current spread / normal spread for that instrument and session. | Stays 1.0 and the penalty never fires. |

Two liveness traps:

- **Ticking is not freshness.** A feed replaying stale data still ticks. Derive
  `stale_time_seconds` from the *source-side* timestamp, not from local arrival time.
- **The engine cannot detect its own absence.** If the collector dies, nothing calls
  `audit_and_de_risk`, no report is produced, and the last mandate stands forever. Put a
  watchdog on the collector, separate from this engine.

## 2. Scoring

$Q = \operatorname{clamp}\left(100 - \sum \text{penalties},\ 0,\ 100\right)$, with the
default penalty table in `references/standards.md`.

Any metric that is `NaN`, infinite, negative, non-positive (spread multiplier) or of the
wrong type short-circuits the whole score to $0$, forces Tier 3, sets
`metrics_valid=False`, and records `INVALID_METRIC:<field>` in `triggered_conditions`.
This is deliberate: every penalty is guarded by a `>` comparison, and every `>`
comparison against `NaN` is `False`, so *skipping* an unevaluable penalty scores the
worst possible input as a perfect feed.

Classification uses the exact score. `data_quality_score_pct` is the same value floored
to two decimals, so a reported number can never look better than the tier assigned.

## 3. Tier classification and action mapping

| Tier | Score | `action_mandate` | `position_sizing_factor` | `allow_new_entries` | `allow_risk_reducing_exits` | `cancel_resting_orders` | `flatten_positions` |
|---|---|---|---|---|---|---|---|
| 0 | $Q \ge 90$ | `ALLOW_FULL_TRADING` | 1.0 | yes | yes | no | no |
| 1 | $70 \le Q < 90$ | `REDUCE_SIZE_50_PCT` | 0.50 | yes | yes | no | no |
| 2 | $40 \le Q < 70$ | `BLOCK_NEW_ENTRIES` | 0.0 | no | yes | yes | no |
| 3 | $Q < 40$ | `EMERGENCY_HALT_AND_FLATTEN` | 0.0 | no | yes | yes | yes |

`position_sizing_factor` is `0.0` at both Tier 2 and Tier 3 and is a **new-entry**
multiplier. Integrating as `qty = qty * report.position_sizing_factor` on every order
suppresses the exits Tier 2 explicitly permits. Gate on the booleans:

```python
if order.reduces_exposure:
    allowed = report.allow_risk_reducing_exits
else:
    allowed = report.allow_new_entries
    qty = qty * report.position_sizing_factor
```

## 4. Recovery hold (anti-flap)

`recovery_hold_seconds` implements escalate-fast / recover-slow:

- A worse tier applies on the observation that produces it, and cancels any pending
  recovery timer.
- A better tier starts a timer. It applies only once the improvement has persisted for
  the full hold. Any relapse to the held tier or worse restarts the timer from scratch.
- While a de-escalation is withheld, `tier_held_by_recovery` is `True` and
  `instantaneous_tier` carries the un-held reading — log both, or the operator sees a
  Tier 3 with clean-looking telemetry and no explanation.

Left at the default `0.0`, the engine is memoryless. Calibrate the hold against the
duration of your feed's observed transient stalls, not against a round number.

`reset(symbol)` clears the held state after an operator has remediated a feed;
`reset()` clears every symbol. State is per symbol, in memory, guarded by a lock, and
grows with the symbol universe — call `reset()` when retiring symbols.

## 5. Executing Tier 3

Tier 3 is a mandate to reduce exposure. It is **not** an instruction to send market
orders priced off the feed the engine has just declared unusable, and MiFID II RTS 6
Article 14(3) requires that a trading system be shut down "without creating disorderly
trading conditions". A defensible sequence:

1. Cancel resting orders first — cancellation does not require a trustworthy price.
2. Re-check quality against a *secondary* source before sending exits. See
   `vendor-outage-fallback-data-source-hierarchy` and
   `multi-source-price-reconciliation-tie-breaking`.
3. Flatten with limit orders bounded by a price collar derived from the last *trusted*
   mark, escalating to market only under an explicit, logged policy.
4. If no trustworthy price exists on any source, escalate to a human rather than
   crossing an unknown spread.

## 6. Audit logging

Log `symbol`, `data_quality_score_pct`, `de_risking_tier`, `instantaneous_tier`,
`tier_held_by_recovery`, `metrics_valid`, `penalty_breakdown` and
`triggered_conditions` on every evaluation, and route the tier transitions to the same
alerting channel as the rest of the risk stack. `penalty_breakdown` is what lets a
post-incident review answer "which metric drove the halt", which prose notes cannot.
See `structured-logging-for-post-incident-forensics`.

## 7. Calibration

Before trading on the defaults, replay a week of recorded feed telemetry through the
engine and count the tier transitions. A feed whose normal staleness exceeds
`stale_grace_seconds` sits permanently in Tier 1; a `spread_grace_multiple` set below
the instrument's normal open/close spread widening de-risks at every session boundary.
Both are calibration failures, not detections.
