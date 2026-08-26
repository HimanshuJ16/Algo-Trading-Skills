# Workflows for Market Data Simulation

Full procedure behind `SKILL.md`. Steps 1–5 map to the numbered workflow there.

## 0. Decide what the fixture is for

This generator answers "does my code handle a tick correctly?" — never "does my
strategy make money?". If the question is the second one, stop here and use recorded
data (`market-data-replay-harness-for-integration-testing`). A GBM path has i.i.d.
Gaussian increments by construction; profit on it is fitted noise, and it is dangerous
precisely because it produces a number that looks like a result.

Then fix the two parameters that cannot be inferred from a price:

- **`seconds_per_year`** — the clock the annualised $\mu$ and $\sigma$ are quoted on.
- **`price_tick_size`** — the venue's minimum price increment.

Neither is derivable from `initial_price`, and neither has a default that is right for
every instrument.

## 1. Configure and validate

`SimulationConfig` validates on construction and again at generation time (so a config
mutated in between cannot reach the price loop). Everything that affects output is a
field: two equal configs produce identical streams, in any process.

| Condition | Action | Why |
|---|---|---|
| `initial_price` $\le 0$ or non-finite | raise | GBM is multiplicative: $\exp(\cdot)$ cannot lift a non-positive price back above zero. |
| `volatility_sigma` $< 0$ | raise | A negative $\sigma$ only mirrors the Gaussian draw. It is a configuration error, not a valid input, and it silently produces a plausible path. |
| `num_steps` not an `int`, or $\le 0$ | raise | A float step count reaches `range()` as an opaque `TypeError` several frames deeper. |
| `time_step_sec` $\le 0$ | raise | $\Delta t \le 0$ makes $\sqrt{\Delta t}$ a `math domain error` with no indication of which field caused it. |
| `time_step_sec` below 1 ns | raise | Timestamps are integer nanoseconds; a finer step would emit duplicates. |
| `spread_bps` $< 0$ | raise | A negative spread crosses the book. |
| `spread_bps` $\ge 20{,}000$ | raise | A half-spread $\ge 100\%$ of the mid drives the bid to zero or below. $20{,}000$ bps is a $200\%$ quoted spread. |
| `price_tick_size` $\le 0$, `seconds_per_year` $\le 0$ | raise | Both are divisors. |
| `price_tick_size` needs more than 12 decimals, or is not an exact decimal (e.g. `1/3`, `3 * 1e-8`) | raise | Quantising to fewer decimals would silently snap prices onto a coarser grid than the caller asked for — a lie about the venue being simulated. |
| `price_tick_size` $>$ `initial_price` | raise | Every mid would quantise to zero or to a grid point a whole tick from the true price. |
| `initial_price` $< 100 \times$ `price_tick_size` | **accept**, warn | Quantisation error exceeds $0.5\%$ of the price. Legitimate for a genuinely low-priced instrument, usually a wrong tick size. |
| `max_depth` $<$ `min_depth`, `min_depth` $< 0$ | raise | An inverted or negative depth range is not a book. |
| Any parameter NaN or $\pm\infty$ | raise | A non-finite input propagates silently through `exp()` and poisons every downstream statistic. |
| `random_seed` is `None` | **accept**, flag, warn | Legitimate for a throwaway run. The report sets `deterministic=False` and a warning is logged, so it cannot be mistaken for a fixture. |

## 2. Walk the price

$$S_{i} = S_{i-1} \exp\!\left[\left(\mu - \tfrac{1}{2}\sigma^{2}\right)\Delta t + \sigma\sqrt{\Delta t}\,Z_i\right], \qquad \Delta t = \frac{\texttt{time\_step\_sec}}{\texttt{seconds\_per\_year}}$$

Two things go wrong here in practice.

**Quantising the state.** It is tempting to round $S_i$ to a display precision inside
the loop. Doing so:

- biases the walk, because the rounding error is carried into every later step rather
  than discarded; and
- destroys any instrument priced below half a tick. At 4 decimals, a \$0.00005 token
  rounds to $0.0$ on the first tick and stays there: $0 \times e^{(\cdot)} = 0$ for
  every subsequent step. The output is an all-zero feed whose `bid == ask == 0`, and a
  "prices are non-negative" assertion passes on it.

Carry the state at full precision; quantise on emission only. `log_return` is exposed
on each tick so volatility can be measured on the unquantised path.

**Choosing the clock.** $\Delta t$ is not determined by `time_step_sec` alone. With
$\sigma = 0.20$ and a 1-second step:

| Clock | $\Delta t$ | Per-step $\sigma$ | Volatility per elapsed wall-clock second |
|---|---|---|---|
| Equity RTH ($5{,}896{,}800$ s/yr) | $1.70 \times 10^{-7}$ | $8.24 \times 10^{-5}$ | $0.20 \times 2.3126 = 0.4625$ |
| Continuous ($31{,}536{,}000$ s/yr) | $3.17 \times 10^{-8}$ | $3.56 \times 10^{-5}$ | $0.20$ |

Simulating `BTC-USD` on the equity clock therefore delivers a path $2.31\times$ as
volatile per elapsed second as requested. `realized_wall_clock_annualized_volatility`
on the report is the figure that reveals this; `realized_annualized_volatility` cannot,
since it is annualised on the same clock the run used.

Guard the exponent: if the path leaves the representable range (an absurd
$\sigma\sqrt{\Delta t}$ combination), raise naming the step rather than emitting `inf`.

## 3. Build the quote

With $\tau =$ `price_tick_size` and half-spread fraction
$\delta = \texttt{spread\_bps}/20{,}000$:

$$P_{\text{mid}} = \text{round}\!\left(\frac{S_i}{\tau}\right)\tau, \qquad P_{\text{bid}} = \left\lfloor \frac{S_i}{\tau} - \delta\frac{S_i}{\tau} \right\rfloor \tau, \qquad P_{\text{ask}} = \left\lceil \frac{S_i}{\tau} + \delta\frac{S_i}{\tau} \right\rceil \tau$$

- **Floor the bid, ceil the ask.** These give the tightest on-grid quote that is no
  narrower than requested. Rounding either inward produces a spread tighter than asked
  for — understating transaction costs — and can collapse the two to equality.
- **Guarantee at least one tick.** When $\delta = 0$ and $S_i$ lands exactly on a grid
  point, floor and ceil coincide; push the ask up one tick. Combined with the above,
  $P_{\text{bid}} < P_{\text{ask}}$ holds unconditionally.
- **Flag, don't hide, the tick constraint.** When the requested spread is narrower than
  one tick, set `tick_constrained` on the tick and count it on the report.
- **Guard the bid.** If $P_{\text{bid}} \le 0$ the mid has fallen within a half-spread
  of zero; raise naming the step rather than publishing a non-positive bid.
- **Derive the decimal precision from the tick's decimal exponent, not its magnitude.**
  A \$0.25 futures tick needs **two** decimals even though it exceeds \$0.1; rounding a
  0.25-grid price to one decimal moves it off the grid.

Expect the realised spread to exceed the request by up to one tick — that is the price
of the widen-only rule. Read `mean_quoted_spread_bps`; do not assume `spread_bps`.

## 4. Synthesise depth, and keep it out of the price stream

Depth is drawn uniformly from `[min_depth, max_depth]` by a **second** `random.Random`
derived from the same seed. This matters for a reason that has nothing to do with
realism: if one stream fed both, changing the depth model would shift every subsequent
Gaussian draw and silently rewrite the price path of every stored regression fixture.
Independent streams make the depth model free to change.

Depth here is uniform noise. It is uncorrelated with price, spread, volatility and time
of day, and nothing should be concluded from it beyond "the field was populated".

## 5. Timestamps and reporting

**Timestamps.** Compute $t_i = t_{\text{start}} + i \times \Delta t$ in **integer
nanoseconds** from the tick index:

- Iterative float addition accumulates error ($1577836800.0 + 0.001 \times 2$ is already
  $1577836800.0019999$).
- A float epoch second resolves to only ~$238$ ns at a 2020 epoch, so any spacing below
  ~$0.24\ \mu$s collapses to duplicate timestamps — silently, in a *tick* simulator.

`timestamp_nanos` is authoritative; `timestamp_epoch` is a lossy convenience view.
Tick $i$ carries $t_{\text{start}} + i\Delta t$, so its timestamp is the instant its
price was reached, not the instant before.

**Reporting.** Statistics cover emitted ticks only — `initial_price` seeds the walk and
is never emitted, so folding it into `min_price`/`max_price` would report a price that
appears in no tick. The report carries both realised volatilities, the realised mean
spread, the tick-constrained count and a `deterministic` flag.

**Memory.** `generate_synthetic_tick_stream` retains every tick by default. For long
runs either iterate with `iter_synthetic_ticks()` and consume ticks as they arrive, or
pass `retain_ticks=False` to get the statistics without the list.
