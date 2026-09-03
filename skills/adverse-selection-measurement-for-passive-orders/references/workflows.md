# Deep Workflow Reference — adverse-selection-measurement-for-passive-orders

SKILL.md is the **interface contract**; this file holds the **engineering
rationale** and full procedure.

## Full procedure

### 1. Filter to passive fills

Adverse selection is a *passive* phenomenon. Pull fills from the broker FIX
logs or EMS database and exclude every active/aggressing order (market orders,
sweeps, crossings). A common filter:

```python
passive_fills = [f for f in fills if f.exec_type in ("LIMIT", "PEGGED", "ICEBERG")
                 and f.liquidity_flag == "ADDED"]
```

Wrap each surviving row in a `PassiveFill`; construction validates `side`,
`fill_price`, `quantity`, and `timestamp`.

### 2. Prepare the market mid series

```python
# ts: strictly ascending, finite, seconds since epoch (same base as fills).
# mids: finite, positive. The window must cover:
#   - from (min(fill_ts) - buffer)   # backward: as-of mid for the earliest fill
#   - to   (max(fill_ts) + max(horizons))  # forward: every horizon resolves
```

The engine validates: equal length, non-empty, finite, positive mids, strictly
ascending timestamps. A non-zero `missing_pre_fill` or `truncated` count means
the window is too narrow — extend it, do not "fix" the verdict.

Every mid the engine reads is the **prevailing** one: the last observation at
or before the timestamp it is used for, for the fill-time reference and for
each horizon alike. It is never the nearest in time, which could resolve to an
observation *after* `fill_ts + h` and silently lengthen the effective horizon.
The series therefore does not need to be evenly sampled, but it does need to be
dense enough that the prevailing mid at each horizon is genuinely current —
`max_mid_staleness_sec` is what enforces that, and it is off unless you set it.

### 3. Configure the engine

```python
config = MarkoutConfig(
    horizons_sec=[0.1, 1.0, 5.0, 60.0],
    markout_basis="fill_to_mid",   # or "arrival_to_mid" to isolate drift
    quantity_weighted=True,        # share-weighted MEAN only (not the quantiles)
    require_asof_mid=True,         # no-lookahead guard, applies to both bases
    max_mid_staleness_sec=2.0,     # refuse a mid older than this; None = off
)
engine = MarkoutEngine(config)
```

### 4. Evaluate

```python
report = engine.evaluate_fills(passive_fills, ts, mids)
```

### 5. Read the report

```python
{
  "total_fills_analyzed": 1200,
  "fills_used": 1198,
  "missing_pre_fill": 2,         # 2 fills had no as-of mid -> skipped
  "stale_asof_mid": 0,           # 0 fills had an over-age fill-time mid
  "horizons_sec": [0.1, 1.0, 5.0, 60.0],
  "evaluable_horizons": 4,       # all 4 produced a markout
  "average_markouts_bps": {"0.1": -3.2, "1.0": -8.1, "5.0": -12.4, "60.0": -5.0},
  "stats": {
    "0.1": {"count": 1198, "mean_bps": -3.2, "median_bps": -1.0,
            "p25_bps": -6.0, "p75_bps": 2.0, "std_bps": 18.4,
            "truncated": 0, "stale": 0},
    ...
  },
  "is_toxic": true,
  "has_sufficient_data": true,
  "toxicity_ratio": 1.0,
  "markout_basis": "fill_to_mid"
}
```

- `has_sufficient_data` / `evaluable_horizons` — **read these first**. With
  `evaluable_horizons == 0` nothing was measured and `is_toxic=False` is
  vacuous, not a clean bill of health; the message says `INSUFFICIENT DATA`.
- `average_markouts_bps[h]` — the headline curve (backward-compat).
- `stats[h]` — the distribution; **read this, not just the mean**. Only
  `mean_bps` honours `quantity_weighted`; the quantiles and std are unweighted.
- `missing_pre_fill` / `stale_asof_mid` / `stats[h].truncated` /
  `stats[h].stale` — data-availability audit; all four must be zero on a clean
  window.

### 6. Diagnose and route

| Curve shape | Diagnosis | Route to |
|---|---|---|
| Sharp negative at ≤100 ms | Stale-quote latency arbitrage | `tick-to-trade-latency-measurement`; tighten cancellation |
| Negative slope over 1–5 s | Immediate informed-flow selection | `queue-position-modeling-for-passive-orders`; re-skew quotes |
| Negative over 1–30 min | Directional adverse selection (alpha wrong) | Re-examine the signal, not the plumbing |
| Positive short, negative long | Spread capture but leaks over holding period | Shorter holding period / faster scratch-outs |
| `arrival_to_mid` neg, `fill_to_mid` pos | Earning spread but mid drifts against you | Pure adverse selection — reduce resting size |

### 7. Gate / act

A persistently toxic curve is a candidate trigger for a strategy-level kill
switch (`kill-switch-and-drawdown-circuit-breakers`) or a quote-skew widening.

Do **not** gate on `is_toxic` alone. Two separate reasons:

1. **Check `has_sufficient_data` first.** `is_toxic=False` on a run where no
   horizon was evaluable means "not measured", not "healthy". A gate that reads
   the boolean without this check silently treats a broken data pipeline as a
   passing result — the failure mode is that the strategy keeps quoting through
   exactly the outage that stopped you measuring it.
2. **Then read `toxicity_ratio` and the per-horizon curve.** A single negative
   horizon among six is not actionable, and the curve's *shape* selects the
   remediation (section 6). Note that `toxicity_ratio` is a share of the
   evaluable horizons, so it is not comparable day-over-day unless the same
   horizons were evaluable on both days.

## Pipeline diagram

```
   FIX / EMS fills ──► filter passive ──► PassiveFill[]
                                              │
   Market data ──► (ts, mids) ──► validate ──┤
                                              │
                                              ▼
                               ┌────────────────────────┐
                               │      MarkoutEngine      │
                               │  for each fill:         │
                               │    as-of mid (no LA)    │──► None? missing/stale++
                               │    for each horizon h:  │
                               │      prevailing(t+h)   │──► None? truncated/stale++
                               │      markout_bps(side)  │
                               │  aggregate per horizon  │
                               │  mean/median/p25/p75   │
                               │  verdict over EVALUABLE │
                               └────────────┬───────────┘
                                            │
                                            ▼
                                AdverseSelectionReport
                                            │
                          ┌─────────────────┼─────────────────┐
                          ▼                 ▼                 ▼
                   average curve      distribution stats   toxicity verdict
                          │                 │                 │
                          └─► diagnose shape ┘                 │
                                           │                    │
                                  route to latency / queue /   │
                                  alpha skills                 ▼
                                                       gate / kill switch
```

## Failure modes & escalation

| Symptom | Probable cause | Action |
|---|---|---|
| `evaluable_horizons == 0` | Window too short / all fills skipped | **Not a healthy verdict.** Extend the window and re-run; never gate on it |
| `missing_pre_fill > 0` | Market data starts after the earliest fill; or clock skew | Extend window backward; fix clock alignment |
| `stale_asof_mid > 0` or `stats[h].stale > 0` | Prevailing mid older than `max_mid_staleness_sec` — session gap, halt, dead feed | Fix the feed or exclude the period; do **not** relax the bound to zero the counter |
| `stats[h].truncated > 0` | Market data ends before `fill_ts + h` | Extend window forward by `max(horizons)` |
| Mean negative, median positive | Few badly-selected fills dominate the mean | Gate on median; report IQR; inspect tails |
| `quantity_weighted` flips verdict vs unweighted | Large fills selected differently | Route size dimension to queue-position modelling |
| `arrival_to_mid` and `fill_to_mid` disagree on sign | Price improvement masking adverse drift | Trust `arrival_to_mid` for the adverse-selection diagnosis |
| Curve wildly noisy across days | <30 fills/horizon, or heterogeneous symbols mixed | Stratify by symbol/day; report median not mean |
| `is_toxic` flips between runs | Non-deterministic input (unsorted fills, clock skew) | Freeze the fill ledger + market snapshot; pin and re-run |
| All horizons ≈ 0 bps | Mid is stale/flat (illiquid name) | Set `max_mid_staleness_sec` so the stale mids are refused rather than scored as 0 bps; fix the mid via `multi-source-price-reconciliation-tie-breaking` |
| Short-horizon curve looks like the long-horizon curve | Mid series too sparse for the short horizons — every horizon resolves to the same prevailing observation | Increase mid-series resolution, or drop horizons below the quote-update cadence; they cannot be measured |

## Integration with execution-quality reporting

Persist the report alongside the day's TCA:

```json
{
  "adverse_selection": {
    "date": "2026-08-09",
    "symbol": "AAPL",
    "fills_used": 1198,
    "missing_pre_fill": 0,
    "stale_asof_mid": 0,
    "horizons_sec": [0.1, 1.0, 5.0, 60.0],
    "evaluable_horizons": 4,
    "average_markouts_bps": {"0.1": -3.2, "1.0": -8.1, "5.0": -12.4, "60.0": -5.0},
    "is_toxic": true,
    "has_sufficient_data": true,
    "toxicity_ratio": 1.0,
    "markout_basis": "fill_to_mid",
    "quantity_weighted": true
  }
}
```

This makes the verdict reproducible and comparable day-over-day — a regression
in the curve (e.g. 1 s markout -8 → -15 bps) is an early warning that quote
selection is degrading, before it shows up in P&L.

## Production implementation reference

- Engine: `scripts/adverse_selection_measurement_for_passive_orders.py`
  (`MarkoutEngine`, `MarkoutConfig`, `PassiveFill`, `AdverseSelectionReport`,
  `HorizonStats`).
- Tests: `scripts/test_adverse_selection_measurement_for_passive_orders.py`
  (46 unit tests).
- Operational checklist: `assets/checklist.md`.

## Cross-references

- `post-trade-execution-quality-scorecard` — parent TCA scorecard.
- `execution-slippage-attribution-timing-vs-sizing` — slippage decomposition.
- `implementation-shortfall-minimization` — active-order counterpart.
- `queue-position-modeling-for-passive-orders` — explains size-dependent selection.
- `tick-to-trade-latency-measurement` — remediates short-horizon toxicity.
- `clock-skew-correction-for-tick-timestamps` — the clock alignment assumed here.
- `kill-switch-and-drawdown-circuit-breakers` — acting on a toxic verdict.
- `real-time-liquidity-risk-monitoring` — live complement to this post-trade measure.
