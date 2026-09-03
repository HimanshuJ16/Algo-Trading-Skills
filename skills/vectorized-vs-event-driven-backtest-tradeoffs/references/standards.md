# Backtesting Methodology Standards — vectorized-vs-event-driven-backtest-tradeoffs

## Engine architecture comparison

| Engine architecture | What it can represent | What it assumes away | Appropriate use |
|---|---|---|---|
| Vectorized (array) | Exposure known ahead of the run; cost as a function of turnover | Unfilled orders, queue position, path-dependent exit logic, fill latency unless modelled as an explicit shift | Parameter sweeps, alpha screening, ranking candidates |
| Event-driven (loop) | Order state, fill latency, conditional fills, path-dependent exits, per-fill cash accounting | Whatever its own fill model omits — none of these engines has a free lunch | Final sign-off before capital, any strategy with a blocking feature |

The **blocking** features — path-dependent stops and passive/limit orders — are not
accuracy trade-offs. A vectorized engine cannot represent them at all:

- A path-dependent stop makes the exposure at bar t a function of the realized path
  since entry, so the exposure vector does not exist before the run. Libraries that
  advertise vectorized stop handling (VectorBT and similar) JIT-compile the loop
  with Numba. That is a faster event loop, not array algebra.
- A limit order may not fill. A vectorized engine has no representation of an
  unfilled order, so it applies the exposure unconditionally.

## Speed: what is actually measured

There is no universal speedup constant, and this skill does not publish one. The
ratio scales with how many discrete events the loop must process per unit of
vectorizable work, so a monthly rebalance and a per-bar signal are not comparable
workloads.

Verified third-party benchmarks, both secondary sources with published methodology:

| Comparison | Workload | Result | Source |
|---|---|---|---|
| Moonshot (vectorized) vs Zipline (event-driven) | Piotroski F-Score factor strategy, top 1,000 US equities, daily bars, monthly rebalance, 2012–2022, M1 Pro | ~2–3 s vs ~15–20 s, i.e. **~6–8×** | [QuantRocket, "Why Backtests Run Fast or Slow"](https://www.quantrocket.com/blog/backtest-speed-comparison/) |
| VectorBT (NumPy/Numba) vs Backtrader (event-driven) | 12-month momentum rotation, 500 S&P constituents, monthly rebalance, 2019–2024, macOS ARM | 0.7 s vs 14.2 s, i.e. **~20×**, at identical portfolio values and trade counts | [Pickuma, "Backtrader vs VectorBT vs Zipline-Reloaded, Benchmarked"](https://pickuma.com/for-dev/python-backtesting-frameworks-backtrader-vectorbt-zipline-2026/) |

This module's own two engines, measured on random-walk closes with a per-bar signal:

| Bars | Vectorized | Event-driven | Ratio |
|---|---|---|---|
| 5,000 | 0.053 ms | 2.45 ms | 46× |
| 50,000 | 0.96 ms | 26.8 ms | 28× |
| 250,000 | 12.8 ms | 156 ms | 12× |

Single runs on one machine, reported to show the order of magnitude and its
variability — not as a benchmark. `speedup_factor` is `None` below 5,000 bars
because a sub-millisecond workload measures timer resolution rather than the
engines.

**Superseded claim.** Version 1.0.0 of this skill stated that vectorized engines are
"1,000× faster" and set a "≥50× speedup" verification gate. No source located
supports either figure, both published benchmarks above are one to two orders of
magnitude below the first, and the v1.0.0 implementation that carried these claims
was a pure-Python loop in both engines which measured **0.78×** — slower than the
event-driven engine it was said to beat. Both claims are withdrawn.

**Superseded claim.** Version 1.0.0 also stated that event-driven fill mechanics
impose a "10-30% performance haircut". No source supports a universal figure, and
the arithmetic precludes one: cost drag is turnover × cost rate and is unbounded
above. Withdrawn in favour of measuring it per strategy.

## Fill-timing conventions

The default `execution_lag_bars=1` follows the mainstream Python event-driven
engines rather than inventing a convention. `backtesting.py` documents that unless
`trade_on_close=True`, "market orders are filled on next bar's open"; same-bar-close
execution exists but must be requested explicitly
([backtesting.py API documentation](https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html)).
This module fills on the next bar's **close** rather than its open, because it
consumes a close series only; the direction of the assumption — never the signal
bar's own price, unless you ask for it — is the part that matters.

`execution_lag_bars=0` reproduces the vectorized instant-fill assumption and is the
setting under which the two engines are held to bar-for-bar agreement in the tests.

## Sharpe ratio annualization

The √(periods per year) rule assumes i.i.d. returns. Lagged execution induces serial
correlation, under which the rule is biased: Lo (2002) shows monthly Sharpe ratios
cannot in general be annualized by √12, and reports an annual Sharpe ratio
overstated by as much as 65% for a hedge fund with serially correlated monthly
returns — Andrew W. Lo, "The Statistics of Sharpe Ratios", *Financial Analysts
Journal* 58(4), 2002, pp. 36–52
([CFA Institute](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios)).

Consequence for this skill: `sharpe_divergence` between a zero-lag and a lagged
engine is affected by the induced autocorrelation as well as by the economics. Read
`return_drag_pct`, which requires no distributional assumption, alongside it.

## Known residual: cost parity between the engines

With costs enabled the two engines agree only to second order in the cost rate. The
vectorized engine charges cost as a fraction of *equity*; the event-driven engine
charges the same rate on *traded notional at a slipped fill price*. The residual is
O(cost²) per trade — about 1e-7 of equity per trade at 10 bps — and is bounded by a
test rather than asserted away. At zero cost the agreement is exact to `rtol=1e-12`.
