# Workflows for FX Forward & Swap Position Tracking

## 1. Contract ingestion

One `FxContractPosition` per **valuation row**, not per trade.

- An outright forward is one row with `contract_type='FX_FORWARD'` and
  `swap_leg=None`.
- An FX swap is **two rows** sharing a `contract_id`: `swap_leg='NEAR'` and
  `swap_leg='FAR'`, in opposite directions, with the far leg maturing strictly
  after the near leg. The engine rejects any other shape — two same-direction
  legs double the exposure instead of rolling it.
- `notional_base_currency` is positive and expressed in the **base** currency.
  Direction lives in `position_side` (`BUY` = long base). A negative notional
  raises rather than being interpreted as a short.
- `agreed_forward_rate` is the all-in contracted outright, quote per base — not
  points, and not spot.
- `days_to_maturity` is **remaining** calendar days at the valuation date.
  Re-derive it every valuation run; feeding original tenor pins the position at
  trade date and prevents the mark from converging to spot.
- `currency_pair` must equal `f"{base_currency}/{quote_currency}"`. A mismatch
  raises, because an inverted pair silently inverts every rate in the row.

## 2. Market data assembly

`market_rates` is keyed by currency pair:

| Key | Required | Meaning |
|---|---|---|
| `spot` | yes | Units of quote currency per one unit of base currency, > 0. |
| `r_base` | yes | Base-currency simple money-market rate for the tenor, as a decimal. May be negative. |
| `r_quote` | yes | Quote-currency simple money-market rate for the tenor. May be negative. |
| `market_forward_rate` | no | Observed outright for the pair and tenor. When present, the mark uses it. |

One entry carries **one tenor point**. A book holding 1M and 1Y positions in the
same pair is being marked off a single point on the curve — split the audit, or
key the tenors separately, until a real term structure is available.

## 3. Day-count resolution

Performed per currency, never per engine.

| Currency | Basis | Status |
|---|---|---|
| USD, EUR | Actual/360 | Verified — see `references/standards.md`. |
| GBP, JPY | Actual/365 | Verified. JPY is **not** Actual/360 post-LIBOR. |
| anything else | `default_day_count_basis` (360) | Assumed, logged once at WARNING, and to be overridden via `day_count_basis`. |

Passing an integer as `day_count_basis` raises `TypeError`. A single denominator
cannot express GBP/USD or USD/JPY, whose legs accrue differently; forcing one
costs 3.78 pips on a 6-month GBP/USD forward.

## 4. CIRP pricing and choice of mark

$$F_{\text{CIRP}} = S \times \frac{1 + r_q \cdot (T/B_q)}{1 + r_b \cdot (T/B_b)}$$

| Condition | `mtm_basis` | Rate used for the mark |
|---|---|---|
| `market_forward_rate` supplied | `OBSERVED_MARKET_FORWARD` | The observed outright. |
| Not supplied | `CIRP_THEORETICAL` | $F_{\text{CIRP}}$. |

`cirp_forward_rate` is reported either way. The spread between the two fields is
the **cross-currency basis** for that pair and tenor: information about funding
conditions, not an error to reconcile away. Covered interest parity has not held
since 2008 (BIS Working Paper 590).

`calculate_cirp_forward_rate` returns an **unrounded** rate. Rounding at 1e-6 is
100 quote-currency units on a 100mm notional; round at the reporting boundary.

Raises when $1 + r \cdot t \le 0$ on either leg — a deeply negative rate over a
long tenor takes the simple-interest form outside its domain.

## 5. Forward / swap points

$$\text{points} = (F - S) \times \text{pip factor}$$

`pip_factor` is 100 where the quote currency is JPY and 10,000 otherwise, with
per-pair overrides available. Both the market points (`swap_points`) and the
contract's own points (`contract_forward_points`) are reported, so carry can be
compared against what was locked in.

## 6. Mark-to-market valuation

| Step | Expression | Currency |
|---|---|---|
| Maturity cash flow | $\pm N_{\text{base}} \times (F_{\text{valuation}} - F_{\text{contract}})$, positive for `BUY` | quote |
| Discount factor | $1 / (1 + r_q \cdot T / B_q)$ | — |
| Present value | cash flow × discount factor | quote |

The cash flow is denominated in the quote currency, so it is discounted at the
quote-currency rate on the quote currency's own basis. Both `undiscounted_mtm_quote`
and `mtm_pv_quote` are reported; `mtm_pv_quote` is the figure to publish.

At `days_to_maturity = 0` the discount factor is exactly 1.0 and the forward has
converged to spot — the engine also flags anything settling within two business
days as a spot exposure rather than an outright forward.

## 7. Exposure aggregation

Every forward commits **both** currencies:

| Leg | Amount |
|---|---|
| base | $\pm N_{\text{base}}$ |
| quote | $\mp N_{\text{base}} \times F_{\text{contract}}$ |

`net_exposure_by_currency` nets these across the whole book.
`net_exposure_by_maturity_bucket` nets them within each bucket
(`0-1M` / `1M-3M` / `3M-6M` / `6M-1Y` / `1Y+`, by inclusive calendar-day bounds).

Read both. A well-constructed FX swap nets to **zero** at book level while
carrying a full notional of gap risk in each of two buckets — that gap is the
whole point of the trade, and the book-level view cannot see it.

## 8. P&L consolidation

`unrealized_mtm_pv_by_quote_currency` and
`unrealized_mtm_undiscounted_by_quote_currency` are keyed by quote currency.
There is deliberately no single total: USD and JPY cash flows are not additive.

To consolidate, pass `reporting_currency` **and** `reporting_fx_rates` — units
of the reporting currency per one unit of each quote currency. A missing entry
raises rather than silently dropping or mis-scaling a currency.

## 9. Audit output

`FxForwardPositionReport` carries the per-row `valuation_details` (including the
day-count bases actually applied, the pip factor, the discount factor, and which
rate was marked against), the aggregates above, a `warnings` list, and
`audit_notes`. Persist `valuation_details` — the applied conventions are what
make a historical mark reproducible after a convention changes.
