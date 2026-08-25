# Workflows for Execution Slippage Attribution

## 1. Input validation (before any arithmetic)

Attribution runs only on data that survives every check. The engine raises rather than
returning a verdict it cannot justify.

- **Prices** — `decision_price`, `arrival_price`, `average_exec_price` must each be finite
  and strictly positive. A NaN price makes every downstream comparison false, which lands
  the trade in the classifier's "nothing material" branch: the most corrupt possible input
  produces the most reassuring possible output (`ZERO_SLIPPAGE` / `OPTIMAL`).
- **Side** — `'BUY'` or `'SELL'` only, case- and whitespace-insensitive. Anything else is a
  data error. Coercing it to `SELL` flips the sign multiplier, reporting a cost as a gain of
  identical magnitude.
- **Quantities** — `order_qty > 0` and `0 < filled_qty <= order_qty`. `filled_qty` defaults
  to `order_qty` (full fill). A fully unfilled order has no execution price to attribute:
  its entire cost is opportunity cost, which belongs to
  `implementation-shortfall-minimization`.
- **Timestamps** — timezone-aware ISO-8601 (a trailing `Z` is accepted), ordered
  `decision <= arrival <= completion`. Naive timestamps are rejected because a delay
  measured across a DST transition or between venues in different zones is silently wrong.

## 2. Price data ingestion

Ingest the decision price (when the PM committed), the arrival price (when the order reached
the broker/venue), and the quantity-weighted average fill price. All three must be on the
same quotation basis and currency; the engine cannot detect a mismatch.

## 3. Shortfall decomposition

With $\text{Side} = +1$ for BUY and $-1$ for SELL, every term divided by $P_{\text{decision}}$:

$$\text{IS}_{\text{total}} = \text{Side} \cdot \frac{\bar{P}_{\text{exec}} - P_{\text{decision}}}{P_{\text{decision}}} \cdot 10^4$$
$$\text{IS}_{\text{timing}} = \text{Side} \cdot \frac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}} \cdot 10^4$$
$$\text{IS}_{\text{sizing}} = \text{Side} \cdot \frac{\bar{P}_{\text{exec}} - P_{\text{arrival}}}{P_{\text{decision}}} \cdot 10^4$$

Positive is money lost, for buys and sells alike.

**Why $P_{\text{decision}}$ in the sizing denominator.** Normalising the impact leg on
$P_{\text{arrival}}$ is defensible in isolation, but it breaks additivity: the three terms no
longer share a denominator and $\text{IS}_{\text{total}} \neq \text{IS}_{\text{timing}} +
\text{IS}_{\text{sizing}}$. Additivity is the whole point of the decomposition, so the
decision price wins.

**Verify, then round.** The identity is algebraically exact, so check it in full precision
(a residual can only mean a numerical problem) and round afterwards. Report the directly
computed total: summing the two rounded components loses up to one $0.01$ bps ulp against
the true total. Writing `total = timing + sizing` makes the printed numbers agree while
verifying nothing.

## 4. Notional weighting for partial fills

Canonical IS divides by the **intended** notional $Q_{\text{order}} \times P_{\text{decision}}$,
so an order that filled only part way contributes only that fraction of its per-share cost:

$$\text{executed\_is\_contribution\_bps} = \text{IS}_{\text{total}} \cdot \frac{Q_{\text{filled}}}{Q_{\text{order}}}$$

Both numbers are reported because they answer different questions. The per-share figure is
what a trader compares against a per-share benchmark; the weighted figure is what rolls up
into a portfolio-level IS. On a full fill they coincide. On a 40% fill the per-share number
overstates the IS contribution by $2.5\times$.

`is_partial_fill` marks a report whose opportunity cost is missing. Do not quote such a
report as the order's total cost.

## 5. Primary driver identification

Rank by **cost-signed** magnitude, not absolute value — a component that made money is never
a slippage driver. Let $\tau$ be the materiality threshold in bps.

| Condition | Driver | Recommendation |
|---|---|---|
| Neither component $> \tau$, total $< -\tau$ | `FAVORABLE_EXECUTION` | `NO_ACTION_COST_FAVORABLE` |
| Neither component $> \tau$, total $\ge -\tau$ | `ZERO_SLIPPAGE` | `OPTIMAL` |
| Both $> \tau$ and $\lvert \text{timing} - \text{sizing} \rvert \le \tau$ | `BOTH_DRIVERS_MATERIAL` | `REDUCE_DELAY_AND_PARTICIPATION` |
| timing $>$ sizing, at least one $> \tau$ | `TIMING_DRIVEN_SLIPPAGE` | `ACCELERATE_ORDER_DISPATCH` |
| otherwise | `SIZING_DRIVEN_SLIPPAGE` | `REDUCE_PARTICIPATION_RATE_CEILING` |

Two failure modes this table exists to prevent:

- **The tie.** A classifier built from two strict `>` comparisons has no branch for
  $\text{timing} = \text{sizing}$, so a $+50 / +50$ split — 100 bps of real cost — falls into
  the "minimal slippage" branch and is reported as `OPTIMAL`.
- **The favourable leg.** Ranking by $\lvert \cdot \rvert$ lets a $-50$ bps timing *gain*
  outrank a $+20$ bps sizing cost, producing `ACCELERATE_ORDER_DISPATCH` — advice that would
  have forfeited the gain and left the only real cost untouched.

`secondary_driver_material` is set when the non-winning component is *also* materially
adverse, so a desk does not fix one leg and declare victory while most of the cost remains.

## 6. Contribution shares

$$\text{share}_i = \frac{\text{IS}_i}{\lvert \text{IS}_{\text{timing}} \rvert + \lvert \text{IS}_{\text{sizing}} \rvert} \cdot 100$$

Normalising on **gross** cost, not the net total, keeps every share within
$[-100\%, +100\%]$. Dividing by $\lvert \text{IS}_{\text{total}} \rvert$ explodes when the
legs offset: $+500$ bps timing against $-499$ bps sizing gives $50{,}000\%$ and
$-49{,}900\%$. When both components share a sign the gross-cost form reproduces the familiar
split ($+50 / +20 \Rightarrow 71.4\% / 28.6\%$).

## 7. Strategy action feedback

Recommendations are **triage hints for a human**, not control actions:

- `ACCELERATE_ORDER_DISPATCH` — investigate the decision-to-arrival path. Read the bps figure
  next to `delay_seconds` before concluding anything: over a short delay on a liquid name the
  timing component is mostly drift and news, not latency.
- `REDUCE_PARTICIPATION_RATE_CEILING` — the algorithm's footprint while working the order is
  the larger adverse cost. Retune against a backtest
  (`execution-algo-parameter-optimization-via-backtest`), not from a single trade.
- `REDUCE_DELAY_AND_PARTICIPATION` — both legs are material; fixing one leaves most of the
  cost in place.

Never wire these strings directly into live algo parameters. Single-trade attribution fits
noise; aggregate across a statistically meaningful sample first.

## 8. Logging and audit

One line per attributed trade, recorded on the report as `audit_notes`. Routine attribution
logs at INFO; WARNING is reserved for a materially adverse total, so a batch run over 10,000
trades does not emit 10,000 warnings and bury the ones that matter. The report carries the
`materiality_threshold_bps` its verdict was judged against, so a stored verdict stays
reproducible after the desk retunes the threshold.
