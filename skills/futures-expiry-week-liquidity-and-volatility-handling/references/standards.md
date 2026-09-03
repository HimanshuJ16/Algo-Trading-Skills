# Standards — futures-expiry-week-liquidity-and-volatility-handling

## Configuration defaults (calibrate before use)

No regulator, exchange, or standards body mandates a spread ceiling, a depth-based
size haircut, or a roll cutoff for an expiring futures position. The values below
are **this library's defaults**, not requirements; the only hard facts are the
exchange-published ones in the next section. Calibrate each per product and record
the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_spread_ticks_threshold` | 2.0 ticks | Blocks market orders when the quoted spread is **strictly greater** than this. A spread exactly at the threshold is not wide. The unit is *this product's* tick, so the same number is a different currency amount on every contract. |
| `min_depth_ratio_threshold` | 0.30 | Applies the size haircut when `top_of_book_depth_qty / baseline_average_depth_qty` is **strictly less** than this. Both figures must use the same depth convention. |
| `mandatory_roll_dbe_cutoff` | 2 business days | Blocks new entries and mandates a roll at `days_to_expiration <= cutoff` (**inclusive**). See the calibration note below — this default sits *after* CME's own designated roll date. |
| `size_haircut_factor` | 0.50 | Multiplier applied when the depth or quad-witching condition fires. `floor()`ed against the base quantity, so the cap never rounds up. |

**Calibration note on the roll cutoff.** CME's designated Equity Index roll date is
the Monday preceding the third Friday of the expiration month, after which the
second-nearest expiration is identified as the lead month. That is roughly four
business days before expiration, so a cutoff of 2 fires once the depth has already
migrated to the deferred contract. The default is deliberately conservative about
*changing* behaviour, not about market structure; raise it per product.

## Exchange-published facts

| Fact | Source |
|---|---|
| The **Equity Index roll date is the Monday prior to the third Friday** of the expiration month; after the roll date it is customary to identify the second nearest expiration month as the lead month. | [CME Group — Equity Index Roll Dates](https://www.cmegroup.com/trading/equity-index/rolldates.html) |
| **E-mini S&P 500 futures and quarterly options expire at 9:30 a.m. ET** on the third Friday of the Mar/Jun/Sep/Dec cycle, cash settled to the **Special Opening Quotation** of the index, which is built from the opening price of each component stock on expiration Friday. | [CME Group — Equity Index Final Settlement Procedures](https://www.cmegroup.com/trading/equity-index/settlement.html) |
| **E-mini S&P 500 (ES) minimum price fluctuation is 0.25 index points** for an outright trade. Tick size is product-specific — the Micro E-mini quotes calendar spreads in 0.05 index points — so a spread threshold expressed in ticks is not portable between products. | [CME Group — E-mini S&P 500 Contract Specs](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html); [CME Group — Micro E-mini Futures fact card](https://www.cmegroup.com/trading/equity-index/files/cme-micro-e-mini-futures-fact-card.pdf) |
| **CME Single Stock futures** are financially settled to the closing price per share of the stock in the cash market, with **trading terminating at 3:00 p.m. CT on the third Friday** of the contract month; quarterly Mar/Jun/Sep/Dec listings, 100 shares per contract, 0.01-point tick worth $1.00. | [CME Group — FAQ: Single Stock Futures](https://www.cmegroup.com/articles/faqs/faq-single-stock-futures.html) |
| CME **launched Single Stock futures on 27 July 2026** (55 standard and 22 Micro contracts), returning single stock futures to US markets. | [CME Group — CME Group to Launch Single Stock Futures on July 27](https://www.cmegroup.com/media-room/press-releases/2026/6/30/cme_group_to_launchsinglestockfuturesonjuly27.html); [CME Group — Initial Listing of Fifty-Five (55) Single Stock Futures and Twenty-Two (22) Micro Single Stock Futures Contracts](https://www.cmegroup.com/notices/ser/2026/07/ser-9974r.html) |
| **OneChicago, the US single stock futures exchange, ceased trading on 18 September 2020** and withdrew from registration as a national securities exchange for security futures products. Between that date and the CME relisting there were no US single stock futures, so the quarterly third Friday was more accurately called *triple* witching. | [SEC — Order Granting OneChicago, LLC's Request To Withdraw From Registration](https://www.federalregister.gov/documents/2021/02/18/2021-03218/self-regulatory-organizations-onechicago-llc-order-granting-onechicago-llcs-request-to-withdraw-from) |

**Consequence encoded in the engine.** The instruments expiring on a quarterly third
Friday do **not** stop trading together: the expiring E-mini S&P 500 future settles
at the *opening* print, while single stock futures and standard equity options run
to the *close*. `is_quadruple_witching_week` therefore flags a week, not a uniform
deadline; the per-instrument cut-off has to come from that product's own
specification.

**Consequence for the depth baseline.** Because open interest has moved to the
deferred contract by the designated roll date, the expiring contract's own
normal-market depth average is the correct baseline — comparing it against the
deferred contract's depth measures the roll, not the degradation.

## Fail-closed design note

Every threshold in this engine is a `>` or `<` comparison, and IEEE-754 `NaN`
compares `False` against all of them. An unvalidated engine handed a `NaN` spread
answers "market orders are permitted" and handed a missing depth baseline answers
"the book is deep" — the least restrictive report available, produced from the data
it understands least. `FuturesOrderBookState.validate()` therefore raises on
non-finite, negative, crossed, and absent inputs rather than degrading. Earlier
versions clamped the depth baseline with `max(1, baseline)`, which converted an
absent baseline into a depth ratio in the hundreds and silently cancelled the
haircut.

## Known limitations

- **Stateless and single-snapshot.** No smoothing, no multi-session confirmation,
  no memory of the previous audit. Momentary spread blowouts and a genuine
  structural widening look identical to it.
- **No exchange calendar.** `days_to_expiration` is an input, not a computation.
  The engine cannot tell business days from calendar days, nor apply holiday
  calendars — see `global-exchange-holiday-calendar-handling`.
- **Top of book only.** One level is not a market-impact model; a haircut order can
  still sweep several levels. See `liquidity-adjusted-position-sizing`.
- **Advisory.** The engine returns constraints; it places, cancels and routes
  nothing, and enforces no broker, venue, or portfolio risk control.
- **Contract terms change by rule filing.** Re-verify settlement times, tick sizes,
  and listing cycles against the current contract specification rather than caching
  the values quoted here.
