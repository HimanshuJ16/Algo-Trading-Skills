---
name: corporate-action-adjusted-backtesting
description: >-
  Use when a stock split, reverse split or cash dividend puts artificial gaps in a
  backtest's price history and fabricates signals; builds a CRSP-convention
  backward-adjusted series for signals while keeping dividend cash accounted separately.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: corporate-actions, stock-splits, dividends, caf, adjusted-prices, backtesting, point-in-time
  brokers_frameworks: "Pandas; NumPy; Generic Backtester"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a backtest reads historical equity or ETF prices over a window that
contains a stock split, reverse split, or cash dividend. Raw venue prices contain
artificial gaps at every such event — a 4-for-1 split prints as a 75% overnight crash —
so technical indicators computed on them (SMA crossovers, RSI, Bollinger bands, any
return series) fire false signals. Adjusted prices fix the signal side and break the
execution side: they are not the prices anyone traded at, so sizing orders or debiting
cash from them corrupts the portfolio's share count and cash balance.

`CorporateActionAdjuster` resolves that by carrying **both** series on every bar. Each
`AdjustedBarData` exposes the raw OHLCV as printed by the venue, plus an adjusted OHLCV
and the two factors that produced it:

- **`caf`** — the price factor. `CAF_t = prod(alpha_E)` over every event with ex-date
  `E > t`, anchored so the most recent bar has `caf == 1.0`.
- **`volume_caf`** — the share-count factor, built from split events only.

## When NOT to Use

- **You need a total-return series.** Multiplying prices by `1 - D/P` removes the
  ex-date price drop; it does **not** credit the cash. Dividend PnL must be credited
  separately, from the raw close and the position held on the ex-date. Using this
  module's adjusted prices as a return series and *also* crediting dividends
  double-counts them.
- **You are building a continuous futures series.** Rolling contracts stitch on a
  ratio or difference basis at the roll date, not on a corporate action calendar. See
  `synthetic-continuous-futures-contract-construction`.
- **The event is a spin-off, merger, rights issue or return of capital.** Only splits,
  reverse splits and ordinary cash dividends are modelled. A spin-off's factor depends
  on the when-issued value of the distributed security and must be supplied out of band.
- **You need the corporate action data itself.** Ingestion, vendor parity and the
  declaration/ex/record/pay lifecycle belong to
  `corporate-action-event-calendar-integration`.
- **You are reconciling two vendors' already-adjusted series.** Vendors differ on
  dividend treatment and rounding; see
  `vendor-specific-adjustment-methodology-reconciliation`.

## Prerequisites

- A corporate action log with, per event: **ex-date** (not the declaration, record or
  pay date), type, and value. `value` is the *share multiplier* for splits (`2.0` for
  2-for-1, `1.1` for a 10% stock dividend, `5.0` on a `REVERSE_SPLIT` for 1-for-5) and
  the *per-share cash amount* for dividends.
- A **raw, unadjusted** OHLCV series. Feeding an already-adjusted vendor series in and
  adjusting it again applies every factor twice.
- The bar preceding each dividend ex-date must be present in the series — that close is
  the factor's denominator.

## Workflow

1. **Load raw bars and events.** Both `BarData` and `CorporateActionEvent` validate on
   construction: non-finite or negative fields, non-`date` dates, unknown event types
   and non-positive split ratios raise `CorporateActionError` rather than being coerced.
   An unrecognised `event_type` is a hard failure by design — silently skipping it
   leaves the split gap sitting inside a series labelled "adjusted".
2. **Decide the vantage point.** If the series feeds a point-in-time research loop, pass
   `as_of=<simulation date>`. Bars after it and events with a later ex-date are both
   excluded, reproducing the series as it stood that day. Omit `as_of` only for
   present-day analysis where the whole event history is legitimately known.
3. **Compute the factors** with `adjust_bars(bars, as_of=...)`:
   - Split, ratio `R`: `alpha = 1/R`. Reverse split, ratio `R`: `alpha = R`.
   - Cash dividend `D`: `alpha = 1 - D / P_close(last bar strictly before ex-date)`.
     **Not** the ex-date close — that couples the factor to the day's market move.
   - Each `alpha` multiplies every bar with `dt < ex_date`. Events are keyed by date,
     not matched to a bar, so an ex-date on a holiday or halt still applies.
4. **Route the two series to the two consumers.**
   - Signals, indicators, returns, correlations → `adj_open/high/low/close`.
   - Order quantity, cash debit/credit, commission, tick rounding, margin →
     `raw_open/high/low/close`.
   - ADV and liquidity screens → `adj_volume`, which is `raw_volume / volume_caf` and is
     therefore untouched by cash dividends.
5. **Credit dividend cash separately.** On each dividend ex-date, `cash += shares_held *
   D`, taken from the event log and the raw position — never inferred from the adjusted
   price series.
6. **Handle the rejections.** A `CorporateActionError` for a dividend at or above its
   reference close means either bad vendor data or a special/liquidating distribution
   that needs an explicitly supplied factor. Do not clamp it — investigate the event.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Referencing the dividend to the ex-date close.** The CRSP convention that Yahoo
  Finance and MATLAB's `adjustedClosingPrices` both implement divides by the last close
  *preceding* the ex-date. A $2 dividend on a stock that also fell from $100 to $90 that
  session yields 0.9778 under the wrong reference and 0.98 under the right one — and the
  error grows without bound as the ex-date close approaches zero.
- **Adjusting volume by the price factor.** A cash dividend changes the price basis and
  leaves the share count alone. Folding it into the volume factor inflates historical
  share volume by the dividend yield, so every ADV-based liquidity or capacity check
  reads high on exactly the names that pay dividends. CRSP keeps these as two separate
  fields; so does this module.
- **Applying an event only when a bar matches its ex-date.** Ex-dates land on exchange
  holidays, on halted sessions, before the start of a truncated series, and on days a
  vendor's calendar disagrees with yours. Matching `event.ex_date == bar.dt` drops those
  events without a word.
- **Look-ahead through the adjusted series.** A fully adjusted modern series encodes
  every future split and dividend into today's price. A signal computed over it at
  simulated date `T` has seen events that had not been announced at `T`. Use `as_of`.
- **Executing at adjusted prices.** Sizing an order off an adjusted price buys the wrong
  number of shares and debits the wrong cash; the discrepancy compounds silently across
  the backtest and only surfaces as an unexplained PnL gap against live.
- **Double-adjusting.** Most retail data APIs return adjusted closes by default. Confirm
  which series you fetched before adjusting it again.
- **Pre-applying an announced-but-not-yet-ex event.** An event with an ex-date after the
  last bar has not occurred within the sample; applying it rescales the whole series and
  breaks the `caf == 1.0` anchor on the newest bar. This module ignores such events and
  logs the reason at DEBUG.

## Verification

- **2-for-1 split**: ex-date 2025-01-03, raw close $100 → $50. Assert `caf == 0.5` and
  `adj_close == 50.0` on Days 1–2, `caf == 1.0` on and after the ex-date, and
  `adj_volume` doubled before the ex-date.
- **Dividend reference price**: $2.00 dividend ex-date 2025-01-02 with a $100 close on
  01-01 and a $90 close on 01-02. Assert `caf == 0.98`, *not* 0.9778.
- **Volume/price separation**: after that same dividend, assert `volume_caf == 1.0` and
  `adj_volume == raw_volume`.
- **Point-in-time**: with a split on 2025-01-03, `adjust_bars(bars, as_of=date(2025,1,1))`
  must return one bar with `caf == 1.0`; the same call without `as_of` must return
  `caf == 0.5` for that bar.
- **Anchor invariant**: for any event set, the last bar's `caf`, `volume_caf` are `1.0`
  and `adj_close == raw_close`.
- Run `python -m unittest discover -s skills/corporate-action-adjusted-backtesting/scripts`.

## Related Skills

- `corporate-action-event-calendar-integration`
- `adjusted-vs-unadjusted-price-series-pitfalls`
- `vendor-specific-adjustment-methodology-reconciliation`
- `lookahead-bias-elimination`
- `point-in-time-fundamentals-data-joins`
- `synthetic-continuous-futures-contract-construction`
