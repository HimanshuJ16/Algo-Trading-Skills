# Deep Workflow Reference — vectorized-vs-event-driven-backtest-tradeoffs

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Establish the contract before running anything

Both engines consume the same two series and the same timing convention. Getting
either wrong invalidates every number downstream.

- `prices[t]` — the close of bar t. Strictly positive, chronologically ordered,
  finite. Non-positive or non-finite values raise.
- `signals[t]` — the **target exposure as a fraction of equity**, decided at the
  close of bar t, computable from data up to and including bar t. `1.0` fully
  invested long, `-1.0` fully short, `0.0` flat. Not a share count. `|signals[t]|`
  above `max_abs_exposure` (default 1.0) raises rather than silently backtesting
  leverage.
- `periods_per_year` — bars per year, for Sharpe annualization. The 252 default is
  daily US equity bars.

The module cannot verify that `signals[t]` was computable at bar t. That is
`lookahead-bias-elimination`'s job, and it must be done first.

## Full Procedure

### 1. Screen for blocking execution features

```python
rec = selector.recommend_engine(
    trades_per_day=0.4,
    uses_limit_orders=False,
    uses_path_dependent_stops=True,
)
# rec.engine            -> RecommendedEngine.EVENT_DRIVEN
# rec.blocking_reasons  -> non-empty; a vectorized run is not merely optimistic
```

`blocking_reasons` being non-empty forces `EVENT_DRIVEN` regardless of turnover.
These are representability failures, not accuracy failures, and cannot be outvoted
by a low trade count. A weighted score that let them be outvoted is precisely the
v1.0.0 defect: at 3.0 points against a 4.0 threshold, a strategy whose only complex
feature was path-dependent stops was routed to the vectorized engine.

### 2. Read the turnover arithmetic rather than a hidden threshold

`estimated_annual_cost_drag_pct` is
`trades_per_day × periods_per_year × avg_exposure_change_per_trade × (commission + slippage)`,
as a percentage of equity. Compare it against `max_tolerable_annual_cost_drag`
(default 2%/yr) and override the tolerance if your strategy's gross edge justifies
it. At the module defaults — 1 trade/day, 252 bars/yr, full reversal, 10 bps — this
is 25.2% a year.

### 3. Run the fast engine for search

```python
fast = selector.run_vectorized_backtest(prices, signals)
```

NumPy array arithmetic. Exposure over bar `t+1` is `signals[t]`, filled instantly at
bar t's close. Cost is a multiplicative haircut on equity proportional to
`|signals[t] - signals[t-1]|`, charged at the trade — not a flat deduction per
position change, and not subtracted from the bar's return. Equity compounds via
`cumprod`; it is never a sum of arithmetic returns.

Use this for grids, where relative ranking matters more than the absolute level. Do
not promote a parameter set on this number.

### 4. Confirm survivors on the event-driven engine

```python
real = selector.run_event_driven_backtest(
    prices, signals,
    execution_lag_bars=1,       # 0 reproduces the vectorized instant-fill assumption
    rebalance_every_bar=False,  # True holds the target weight, matching w*r exactly
)
```

Per-bar loop, three ordered steps at each bar:

1. **Decide** at the close of bar t; if the target changed, queue it for
   `t + execution_lag_bars`.
2. **Fill** whatever is due at this bar. Size on the price actually paid, not the
   mark — sizing on the mark deploys `target × equity` at a slipped price and buys
   `target × (1 + slippage)` of exposure, quietly running the book levered. Cash is
   debited on a buy and **credited** on a sell.
3. **Mark** to market at the bar's close.

An order still queued when the series ends never fills. That is the honest outcome;
retroactively filling it would be look-ahead.

### 5. Decompose the drag

```python
report = selector.compare_engines(prices, signals, execution_lag_bars=1)
```

Three curves, two attributable gaps:

| Field | Definition | What it tells you |
|---|---|---|
| `frictionless_metrics` | Instant fill, zero cost | What a naive vectorized backtest reports |
| `cost_drag_pct` | frictionless − vectorized | Transaction costs alone, fill assumption held constant |
| `return_drag_pct` | vectorized − event-driven | Fill latency and slippage alone, costs held constant |
| `total_drag_pct` | frictionless − event-driven | Everything the idealised curve loses |

The remedies differ. A large **cost drag** means trade less or trade cheaper — it
scales linearly with turnover, so halving turnover halves it. A large **latency
drag** means the edge decays inside your execution delay; trading less does not fix
that, and sizing down does not either. Only a faster path to the venue, a different
signal horizon, or passive execution will.

`total_turnover` must be read alongside any drag figure. Cost drag is turnover times
the cost rate, so a drag number without its turnover cannot be interpreted or
compared across strategies.

### 6. Validate the harness before believing it

Before trusting any cross-engine gap, confirm the engines are commensurable:

```python
free = DualBacktestEngineSelector(commission_bps=0.0, slippage_bps=0.0)
a = free.run_vectorized_backtest(prices, signals)
b = free.run_event_driven_backtest(
    prices, signals, execution_lag_bars=0, rebalance_every_bar=True
)
assert np.allclose(a.equity_curve, b.equity_curve, rtol=1e-12)
```

With costs, latency and weight drift all switched off there is nothing left to
differ. If this fails, the harness is measuring itself, and every drag figure it
produces is an artefact. This is the check whose absence let v1.0.0 report a
48.94-point "execution drag" that was entirely a units mismatch.

### 7. Audit the timing convention

`equity_curve` is exposed for this. Two runs whose inputs agree up to bar k must
have identical equity through index `k+1`, whatever happens after it. A run that
fails that check is reading the future.

### 8. Measure speed, do not quote it

`speedup_factor` is `None` below `MIN_BARS_FOR_TIMING` (5,000) bars, because timing
a sub-millisecond workload measures timer resolution. Above it, treat the number as
one run on one machine. See `references/standards.md` for measured ranges and for
the withdrawn v1.0.0 speed claims.

## Weight drift, and why the default diverges

The vectorized `w · r` product assumes the target weight is restored every bar. A
fully-invested long satisfies that for free: all equity is in the asset, so the
weight stays at 1.0 without trading. A short does not — at `w = -1`, a return `r`
leaves the realized exposure at `-(1+r)/(1-r)`, not `-1`.

So with `rebalance_every_bar=False` (the realistic default) a short or levered signal
diverges from the vectorized curve **even at zero cost and zero latency**. That is a
modelling difference between a constant-exposure strategy and a
hold-until-signal-change strategy, not a defect, and it is worth knowing which one
your vectorized backtest has been silently assuming.

## Production Implementation Reference

- Reference code: `scripts/engine_selector.py` — `DualBacktestEngineSelector`,
  `RecommendedEngine`, `EngineRecommendation`, `BacktestEngineMetrics`,
  `DualEngineAuditReport`, and the standalone `annualized_sharpe`.
- Automated unit tests: `scripts/test_engine_selector.py` (41 tests).
- Scope limits and withdrawn claims: `references/standards.md`.
