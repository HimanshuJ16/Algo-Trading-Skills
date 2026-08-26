# Standards — market-data-simulator-for-offline-development

## Configuration parameters that cannot be defaulted safely

These are the reference implementation's defaults, **not** industry standards. Two of
them are properties of the instrument, not of the code, and getting either wrong
produces a plausible-looking feed that is quantitatively wrong.

| Parameter | Default | What it actually does |
|---|---|---|
| `seconds_per_year` | `TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH` ($5{,}896{,}800$) | Denominator converting annualised $\mu,\sigma$ into a per-tick $\Delta t$. Wrong value ⟹ wrong volatility per elapsed second. Equity vs continuous differ by $\sqrt{5.35} \approx 2.31\times$. |
| `price_tick_size` | $0.01$ | Grid the emitted mid and quote are snapped to. Must match the venue: too coarse flattens the path, too fine emits quotes no venue would publish. |
| `spread_bps` | $10.0$ | Requested quoted spread, bps of mid. The **realised** spread equals or exceeds it by up to one tick — read `mean_quoted_spread_bps`. |
| `random_seed` | $42$ | `None` produces an irreproducible run; the report sets `deterministic=False` and logs a warning. |
| `min_depth` / `max_depth` | $10.0$ / $500.0$ | Uniform bounds on synthesised top-of-book size. A placeholder, not a liquidity model — see *Known limitations*. |
| `start_timestamp_epoch` | $1{,}577{,}836{,}800$ (2020-01-01T00:00:00Z) | Fixed, deliberately: a wall-clock default makes a seeded run irreproducible. |

## Engineering conventions (house rules, not external mandates)

| Convention | Rationale |
|---|---|
| The GBM state is carried at full float precision; quantisation applies to the emitted value only. | Rounding the state biases the walk, and on an instrument priced below half a tick it snaps the price to zero permanently — every later price is $0 \times e^{(\cdot)} = 0$. |
| Quotes are widened to the enclosing tick boundaries (floor the bid, ceil the ask), never narrowed. | A quote rounded inward is tighter than requested and understates transaction costs. Widening also makes $P_{\text{bid}} < P_{\text{ask}}$ unconditional. |
| A requested spread narrower than one tick is widened to one tick and **flagged**, not silently absorbed. | That is what a tick-constrained instrument does; the count is surfaced as `tick_constrained_quote_count` so it is visible rather than inferred. |
| Price and depth draw from two independent RNG streams derived from the one seed. | A change to the depth model must leave the price path of an existing regression fixture byte-identical. |
| No global RNG is seeded and no wall clock is read. | `random.seed()` reseeds the process-wide generator, hijacking every other consumer of `random` in the test process. Every input lives in the config. |
| Tick timestamps are integer nanoseconds computed from the tick index. | A float epoch second resolves to ~$238$ ns at a 2020 epoch, so sub-microsecond spacing collapses to duplicates; iterative addition also accumulates drift. Matches the `timestamp_nanos` convention in `historical-tick-data-storage-and-compaction`. |
| Report statistics cover **emitted ticks only**. | `initial_price` seeds the walk and is never emitted; folding it into the extremes reports a price present in no tick. |
| Volatility is measured on the unquantised log-returns and reported on two clocks. | Quantisation is a venue display convention, not a property of the process. See the volatility section below. |

## The two volatility figures, and which one is diagnostic

`SimulationReport` carries both. They answer different questions:

| Field | Annualised on | What it can tell you |
|---|---|---|
| `realized_annualized_volatility` | the clock the run used | Self-consistency of the discretisation. Should always match `configured_annualized_volatility` within sampling error. **It cannot detect a wrong clock**, because the clock is an input to both sides of the comparison. |
| `realized_wall_clock_annualized_volatility` | the 365×24h calendar | **This is the diagnostic.** It equals the configured $\sigma$ only when the run used the continuous clock. On the equity clock it comes back ~$2.31\times$ higher — correct for an instrument trading 6.5 h a day, wrong for one trading 24/7. |

Sampling error on the sample standard deviation is approximately $1/\sqrt{2n}$: ~$0.5\%$
at $n = 20{,}000$, ~$1.6\%$ at $n = 2{,}000$. Do not read a $1\%$ discrepancy on a short
run as a calibration fault.

## Market-structure facts underlying the defaults (verified)

### Trading-session clock

**Jurisdiction: United States (NYSE / Nasdaq listed equities).**

The NYSE Core Trading Session runs **09:30–16:00 ET**, i.e. 6.5 hours — NYSE Rule
1.1 defines "Core Trading Hours" as 09:30 ET through 16:00 ET
([NYSE Trading Information](https://www.nyse.com/trade/trading-information),
[NYSE Holidays & Trading Hours](https://www.nyse.com/markets/hours-calendars)).

The exchange observes **10 full market holidays** in a calendar year (2026: New Year's
Day, MLK Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth,
Independence Day observed, Labor Day, Thanksgiving, Christmas), plus early 13:00 ET
closes that are not full closures. Netting weekends and those holidays leaves ~251–252
sessions, which is the origin of the conventional **252 trading days**.

$$252 \times 6.5 \times 3600 = 5{,}896{,}800 \text{ s}$$

**252 is a market convention, not a regulator-published constant**, and the exact count
varies by ±1 with the calendar. Use it as the business-time denominator when $\sigma$ is
quoted in business time — which is the usual convention for equity implied and realised
volatility — and use $365 \times 24 \times 3600 = 31{,}536{,}000$ s for continuously
traded instruments.

### Minimum pricing increment (tick size)

**Jurisdiction: United States, NMS stocks. Does not apply to futures, FX or crypto**,
each of which sets increments by contract or venue rule.

Regulation NMS Rule 612 (17 CFR § 242.612) governs the minimum increment at which a
national securities exchange, association, broker or dealer may display, rank or accept
a bid or offer in an NMS stock:

| Share price | Minimum increment | Source |
|---|---|---|
| Below \$1.00 | \$0.0001 | 17 CFR § 242.612(b) |
| \$1.00 or above | \$0.01 where the Time Weighted Average Quoted Spread over the Evaluation Period exceeded \$0.015; \$0.005 where it was \$0.015 or less | 17 CFR § 242.612(b), as amended 2024 |
| New NMS stock, first operative period | \$0.01 at or above \$1.00 | 17 CFR § 242.612(c) |

Sources: [17 CFR § 242.612](https://www.law.cornell.edu/cfr/text/17/242.612);
[SEC, *Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of
Better Priced Orders*](https://www.sec.gov/rules-regulations/2024/09/regulation-nms-minimum-pricing-increments-access-fees-transparency-better-priced-orders)
(adopted September 2024); [SEC, *Tick Sizes — A Small Entity Compliance
Guide*](https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/tick-sizes).

**Status qualifier — verify before relying on the \$0.005 tier.** The \$0.005 increment
and the spread-based assignment mechanism were adopted in 2024. A petition for review
was denied by the D.C. Circuit on 14 October 2025, so the amendments were upheld rather
than vacated; the SEC has separately issued exemptive relief and signalled that
compliance dates may be adjusted
([Chairman Atkins, 15 October 2025](https://www.sec.gov/newsroom/speeches-statements/atkins-101525-statement-regarding-minimum-pricing-increments-access-fee-caps);
[SEC exemptive order press release](https://www.sec.gov/newsroom/press-releases/2025-130-sec-issues-exemptive-order-regarding-compliance-certain-rules-under-regulation-nms)).
The long-standing \$0.01 / \$0.0001 baseline is stable; the \$0.005 tier's operative
date is not. Confirm the current position against the SEC before using it as a
simulation default, and track venue-level changes with
`exchange-tick-size-regime-tracking`.

Nothing in this skill is a regulatory control and nothing here is mandated by any
regulator. Rule 612 is cited only because `price_tick_size` should be set to a
*realistic* increment, and for NMS stocks that is where the realistic values come from.

## The price process

$$dS_t = \mu S_t\,dt + \sigma S_t\,dW_t \quad \Longrightarrow \quad S_{t+\Delta t} = S_t \exp\!\left[\left(\mu - \tfrac{1}{2}\sigma^{2}\right)\Delta t + \sigma\sqrt{\Delta t}\,Z\right],\; Z \sim \mathcal{N}(0,1)$$

This is the exact solution of the SDE, not an Euler discretisation, so it is unbiased at
any step size and cannot produce a non-positive price. The $-\tfrac{1}{2}\sigma^2$ term
is the Itô correction: without it $\mathbb{E}[S_t] \neq S_0 e^{\mu t}$ and the path
drifts high.

Consequences worth stating, because they bound what the output can be used for:

- Log-returns are i.i.d. Gaussian. No autocorrelation, no volatility clustering, no
  fat tails, no jumps, no leverage effect — none of the stylised facts of real returns.
- Volatility is constant. There is no intraday seasonality, no open/close effect.
- Therefore a strategy that appears profitable on this path has fitted noise.

## Known limitations of the reference implementation

- **Top of book only.** One price level per side. No ladder, no queue, no order flow,
  no impact — nothing on which queue-position or market-impact work can be based.
- **`last_price` is the mid, not a trade print.** No trade-arrival or aggressor-side
  model exists here. A backtest filling at `last_price` pays zero spread.
- **Depth is uniform noise** in `[min_depth, max_depth]`, uncorrelated with price,
  spread, volatility or time of day.
- **No session structure.** No opens, closes, auctions, halts, gaps, holidays or
  bursts; the inter-tick interval is exactly constant.
- **The spread is a constant fraction of the mid**, widened to the grid. Real spreads
  widen with volatility and at the open, and vary with size.
- **Single instrument, single path.** No cross-asset correlation and no multi-symbol
  synchronisation.
- **Realised spread exceeds the request** by up to one tick, by design (widen-only).
  `mean_quoted_spread_bps` reports what was actually produced.
- **Depth and price streams are independent by construction**, which is a
  reproducibility property, not a claim that real depth is independent of price.

## Category

`data-management-global`
