---
name: market-data-simulator-for-offline-development
description: >-
  Use when developing strategy, risk or feed-handler code with no live subscription and
  no recorded session, generating a deterministic synthetic top-of-book stream. A
  synthetic path cannot demonstrate edge.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: market-data, simulator, offline-development, geometric-brownian-motion, synthetic-ticks, bid-ask-spread, reproducible-fixture
  brokers_frameworks: "Python Standard Library; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you need a market-data feed to develop against and don't have one:
no exchange connection, no paid subscription, no recorded session, or simply a CI job
that must not depend on either. `MarketDataSimulatorEngine` emits a deterministic
stream of `SimulatedTick` records — sequence id, integer-nanosecond timestamp,
bid/ask/mid and top-of-book depth — driven by Geometric Brownian Motion and
reproducible from one `random_seed`.

It is a **plumbing fixture**: it exercises the wiring of a tick handler, a sequencer,
a risk check, an order-state machine or a dashboard, at whatever tick rate and price
level you need, without a network. It is not a market model.

## When NOT to Use

- **To validate that a strategy has edge.** A GBM path has no predictable structure by
  construction — increments are independent by definition. Any strategy that appears
  profitable on it has fitted noise, and the result is worse than no result because it
  looks like evidence. Use recorded data.
- **For microstructure, execution or queue research.** The feed is top of book only:
  one price level per side, depth drawn from a uniform distribution uncorrelated with
  price, spread or time. There is no book, no queue, no order flow and no impact. See
  `order-book-depth-processing-l2-l3` and `queue-position-modeling-for-passive-orders`.
- **To test behaviour at market events.** No opens, closes, auctions, halts, gaps,
  holidays or bursts: ticks arrive on an exactly regular grid, which no real feed does.
  Replay a recorded session instead —
  `market-data-replay-harness-for-integration-testing`.
- **To calibrate transaction costs.** The spread is a constant fraction of the mid,
  widened to the tick grid. Real spreads widen with volatility, at the open, and in
  size. See `transaction-cost-analysis-tca-integration`.
- **When you need fat tails or volatility clustering.** GBM has neither. For GARCH
  paths and block bootstrapping see
  `synthetic-data-generation-for-backtest-augmentation`.

## Prerequisites

- `symbol`, `initial_price` (strictly positive), and `num_steps`.
- `drift_mu` and `volatility_sigma`, **annualised**.
- `seconds_per_year` — the clock those annualised parameters are quoted on. Use
  `TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH` (5,896,800 s = 252 days × 6.5 h) for cash
  equities, `SECONDS_PER_YEAR_CONTINUOUS` (31,536,000 s) for crypto and other 24/7
  instruments. There is no correct default for every instrument.
- `price_tick_size` — the venue's minimum price increment, matching the instrument
  (0.01 for most NMS stocks ≥ \$1.00, 0.0001 below \$1.00, 0.25 for CME ES,
  1e-8 for small-denomination crypto). See `references/standards.md`.
- `spread_bps` and a `random_seed`. Leave the seed at `None` only for throwaway runs —
  the report flags such a run as non-reproducible.

## Workflow

1. **Fix the two parameters that cannot be inferred from a price.**
   - `seconds_per_year` (the volatility clock) and `price_tick_size` (the quote grid).
   - **Decision point — the clock is not cosmetic.** The equity and continuous clocks
     differ by $5.35\times$, i.e. $\sqrt{5.35} \approx 2.31\times$ in volatility.
     Simulating a 24/7 instrument on the equity clock produces a path $2.31\times$ as
     volatile per elapsed wall-clock second as the $\sigma$ you asked for. Read
     `realized_wall_clock_annualized_volatility` off the report to confirm which you
     got; `realized_annualized_volatility` cannot tell you, because it is annualised
     on the same clock the run used and so always agrees with the input.

2. **Generate the price path** — the exact solution of $dS = \mu S\,dt + \sigma S\,dW$
   over one step, with $\Delta t = \texttt{time\_step\_sec} / \texttt{seconds\_per\_year}$:
   $$S_{i} = S_{i-1} \times \exp\left( (\mu - \tfrac{1}{2}\sigma^2)\Delta t + \sigma \sqrt{\Delta t}\, Z_i \right), \quad Z_i \sim \mathcal{N}(0,1)$$
   - **Decision point — quantise the output, never the state.** The walk is carried at
     full float precision and rounded only on emission. Rounding $S_i$ before feeding
     it into step $i+1$ biases the path, and on an instrument priced below half a tick
     it snaps the price to zero permanently — every subsequent price is
     $0 \times e^{(\cdot)} = 0$.

3. **Derive the quote from the mid.** With half-spread fraction
   $\delta = \texttt{spread\_bps} / 20{,}000$ (bps → fraction, halved):
   $$P_{\text{bid}} = \left\lfloor \frac{S_i(1-\delta)}{\tau} \right\rfloor \tau, \qquad P_{\text{ask}} = \left\lceil \frac{S_i(1+\delta)}{\tau} \right\rceil \tau$$
   where $\tau$ is `price_tick_size`.
   - **Decision point — widen to the grid, never narrow.** Floor the bid, ceil the ask.
     Rounding either one inward would produce a quote tighter than requested and
     understate transaction costs; it can also collapse $P_{\text{bid}} = P_{\text{ask}}$.
     The realised spread therefore equals or exceeds the request by up to one tick —
     read `mean_quoted_spread_bps` rather than assuming you got `spread_bps`.
   - When the requested spread is narrower than one tick, the quote is widened to the
     venue minimum and the tick is flagged `tick_constrained`. That is what a real
     tick-constrained instrument does; it is reported, not silently absorbed.

4. **Synthesise top-of-book depth** from a **second, independent** random stream, so a
   change to the depth model cannot perturb the price path of an existing regression
   fixture.

5. **Emit the report.** `SimulationReport` carries the path statistics over emitted
   ticks only, both realised volatilities, the realised mean spread, the
   tick-constrained count, and a `deterministic` flag.
   - For long runs use `iter_synthetic_ticks()` or `retain_ticks=False`: the default
     retains every tick in memory.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Seeding the global RNG.** `random.seed(n)` reseeds the process-wide generator, so
  the simulator silently hijacks every other consumer of `random` in the test process
  — and inherits their state when no seed is given. Build a `random.Random(seed)`
  instance instead. This engine seeds no global state and reads no wall clock.
- **A seeded run that still isn't reproducible.** Seeding the RNG but defaulting the
  start timestamp to `time.time()` makes every tick's timestamp differ between runs.
  A fixture is reproducible only when *every* input is in the config.
- **Rounding the price state instead of the emitted price.** See Workflow step 2: on a
  sub-tick instrument this produces an all-zero feed with `bid == ask == 0`, and the
  invariant tests pass because zero is not negative.
- **`bid == ask` from a rounded half-spread.** Rounding the half-spread to fixed
  decimals sends it to zero whenever it falls below half of the last decimal place,
  producing a locked or crossed book that no venue would publish.
- **Annualised σ on the wrong clock.** `time_step_sec` alone does not determine
  $\Delta t$; the seconds-per-year denominator does, and it is a property of the
  instrument, not of the code.
- **Filling backtests at `last_price`.** `last_price` in this feed is the **mid**, not
  a trade print — nothing here models trade arrival or aggressor side. A backtest that
  fills at it pays zero spread and reports costs that cannot occur. Fill at
  `bid_price` / `ask_price`.
- **Reading `min_price` as a tick that happened.** Statistics cover emitted ticks only;
  `initial_price` seeds the walk and is never emitted. A simulator that folds it into
  the extremes reports a price present in no tick.
- **Sub-microsecond timestamps in float seconds.** A float epoch second resolves to
  ~238 ns at a 2020 epoch, so any tick spacing below ~0.24 µs collapses to duplicate
  timestamps, and coarser steps accumulate drift when added iteratively.
  `timestamp_nanos` is the authoritative integer timestamp; `timestamp_epoch` is a
  lossy view.
- **A tick size that doesn't match the instrument.** Too coarse and the path flattens
  onto a handful of grid points or quantises to zero; too fine and you publish quotes
  no venue would accept. The engine rejects a grid coarser than the price and warns
  below 100 ticks of price, but only you know the venue's real increment.
- **Materialising a multi-million-tick run.** The default retains every tick. Stream
  with `iter_synthetic_ticks()` when the run is longer than memory.
- **Treating a GBM backtest as evidence.** See *When NOT to Use*. This is the most
  expensive mistake available here, because it produces a number that looks like a
  result.

## Verification

- Instantiate `MarketDataSimulatorEngine`. Run 1,000 ticks
  ($S_0 = \$100.00$, $\mu=0.05$, $\sigma=0.20$, spread $=10$ bps, $\tau=\$0.01$,
  `random_seed=42`) ⟹ exactly 1,000 ticks, every price strictly positive, every
  $P_{\text{bid}} < P_{\text{ask}}$, and two runs returning identical tick lists
  **including timestamps**.
- **Zero-volatility closed form**: with $\sigma = 0$ the path is deterministic —
  verify $S_n = S_0 e^{\mu n \Delta t}$ to 8 decimal places. This checks the
  discretisation against an independently derived value rather than against itself.
- **Volatility calibration**: over 20,000 ticks at $\sigma = 0.20$, verify
  `realized_annualized_volatility` $\approx 0.20$, and that
  `realized_wall_clock_annualized_volatility` is $\sqrt{31{,}536{,}000/5{,}896{,}800}
  \approx 2.3126\times$ larger on the equity clock and equal on the continuous clock.
- **Sub-tick instrument**: $S_0 = \$0.00005$ with $\tau = 10^{-8}$ ⟹ the price must
  move and stay strictly positive, never collapse to zero.
- **Sub-tick spread**: `spread_bps` of $0$, $10^{-9}$ and $0.5$ on a \$1.00 mid with
  $\tau = \$0.0001$ ⟹ every quote still satisfies bid $<$ ask, flagged
  `tick_constrained`.
- **Grid integrity**: on a $\tau = \$0.25$ futures grid, every emitted bid, mid and ask
  is an exact multiple of $0.25$.
- **Isolation**: seeding `random` before and after a run yields the same caller
  sequence — the engine must not touch the global RNG.
- Negative checks: non-positive `initial_price`, non-positive `num_steps` or
  `time_step_sec`, negative `volatility_sigma` or `spread_bps`, `spread_bps` $\geq
  20{,}000$, non-positive `price_tick_size` or `seconds_per_year`, a `price_tick_size`
  coarser than `initial_price` or not expressible as an exact decimal, a blank symbol,
  and any non-finite parameter must each raise. An invalid config must raise from
  `iter_synthetic_ticks()` itself, not on the first `next()`.
- Run `python -m unittest discover -s skills/market-data-simulator-for-offline-development/scripts` and confirm a 100% pass rate.

## Related Skills

- `market-data-replay-harness-for-integration-testing`
- `synthetic-data-generation-for-backtest-augmentation`
- `historical-tick-data-storage-and-compaction`
- `backtest-determinism-and-reproducibility`
- `execution-realistic-simulation`
