# Workflows for Single Stock Futures (Where Available)

The deep procedure behind `SKILL.md`. Every numeric default named here is a placeholder
unless `references/standards.md` cites a source for it.

## 0. Confirm the contract exists before pricing it

"Where available" is the first gate, not a caveat.

- **NSE India** — stock futures on the F&O-eligible universe, which SEBI revises; a
  name can be excluded from F&O between backtest and live.
- **Eurex** — single stock futures across European and some non-European underlyings,
  in cash-settled and physically deliverable variants. Settlement is a per-contract
  term; read the contract, not the venue.
- **Euronext** — single stock futures on its listed universe.
- **CME** — relisted 27 July 2026, cash-settled, 55 standard and 22 Micro contracts.
- **US, 18 Sep 2020 – 27 Jul 2026** — no venue listed them at all. A universe file
  spanning that gap will price contracts that did not exist.

## 1. Assemble and validate the inputs

`compute_fair_value_and_arbitrage` rejects rather than repairs. Each rejection exists
because the alternative is a confident wrong signal, not an error:

| Input | Rejected when | Failure it prevents |
|---|---|---|
| `spot_price`, `market_ssf_price` | NaN, Inf, `<= 0`, boolean, non-numeric | `max(0.01, nan)` returns `0.01` and `nan >= threshold` is `False`: v1.0.0 turned a NaN spot into a fair value of 0.01 and a confident `CASH_AND_CARRY`. |
| `risk_free_rate_annual`, `short_borrow_rate_annual`, `lending_income_rate_annual` | Outside $(-1, 5)$, or negative for the two fee rates | Percent-versus-decimal: `6` for 6% inflates the forward by $e^{6T}$. |
| `lending_income_rate_annual` | Greater than `short_borrow_rate_annual` | Inverts the band; every price becomes both too rich and too cheap. |
| `lot_size` | Not a positive `int` | A zero or negative notional produces a nonsense margin and a leverage figure of 1.0. |
| `days_to_expiry` | Not an `int`, negative, or over 3650 | An expired or absurd tenor; usually a timestamp differenced in the wrong direction or unit. |
| `settlement_type` | Not an `SSFSettlementType` | A raw string bypasses the physical-delivery warning entirely. |
| `day_count_basis` | Not 365.0 or 360.0 | A silently unsupported convention. |

## 2. Present-value the dividend schedule

For each `DividendEvent` with `0 <= ex_date_days <= days_to_expiry`:

$$\text{PV}(D) = \sum_i D_i \, e^{-r t_i}, \qquad t_i = \frac{\text{ex\_date\_days}_i}{\text{day\_count\_basis}}$$

- Dividends outside the window are **excluded, logged at WARNING, and counted** in
  `excluded_dividends`. Silent exclusion is how a schedule passed in the wrong unit
  produces a fair value that is too high with nothing in the output to show it.
- `ex_date_days == 0` logs a warning: if the quoted spot is already ex-dividend,
  including the dividend double-counts the drop.
- $\text{PV}(D) \ge S$ raises `SSFInputError`.
- Only **cash dividends** are modelled. Do not encode a bonus, split, rights issue or
  spin-off as a `DividendEvent`; those adjust the contract through an exchange
  adjustment factor on the lot size and strike, which this module does not implement.

## 3. Build the no-arbitrage band

With $\text{base} = S - \text{PV}(D)$ and $T = \text{days\_to\_expiry} / \text{day\_count\_basis}$:

| Quantity | Expression |
|---|---|
| Ceiling (`no_arbitrage_upper_bound`) | $\text{base} \cdot e^{(r - s_{\text{lend}})T}$ |
| Floor (`no_arbitrage_lower_bound`) | $\text{base} \cdot e^{(r - s_{\text{borrow}})T}$ |
| Reference (`theoretical_fair_value`) | $\text{base} \cdot e^{rT}$ |

The ceiling is what a cash-and-carry can defend: the long funds at $r$ and earns only
*contracted* lending income. The floor is what a reverse cash-and-carry can defend: the
short pays the borrow fee. The reference is the zero-borrow-cost case, reported for
continuity and **never used as a trigger**.

When $s_{\text{borrow}} = s_{\text{lend}} = 0$ all three coincide and the band reduces
to the textbook forward.

## 4. Screen against the widened edges

With $c = \text{arbitrage\_cost\_threshold\_pct} / 100$:

```
if  F_market >= ceiling * (1 + c):   CASH_AND_CARRY          # buy spot, sell SSF
elif F_market <= floor   * (1 - c):  REVERSE_CASH_AND_CARRY  # short spot, buy SSF
else:                                NEUTRAL
```

- Both triggers are **inclusive** at the exact boundary.
- Comparisons run on **unrounded** values; rounding is applied to report fields only.
- `gross_edge_pct` is the signed excess beyond the violated edge, zero when neutral.
  This is the tradeable number.
- `mispricing_pct`, measured against the carry-neutral reference, is reported for
  continuity and does **not** drive the signal. On a hard-to-borrow name it will be
  large and negative while the verdict is correctly `NEUTRAL`.

### Acting on a signal

| Signal | Legs | What must be true before it is executable |
|---|---|---|
| `CASH_AND_CARRY` | Buy spot, sell SSF | Funding available at or below the `r` you priced with, for the whole tenor. If the contract is physically settled, an unwind plan or the cash to deliver at expiry. |
| `REVERSE_CASH_AND_CARRY` | Short spot, buy SSF | **A located borrow** at or below `short_borrow_rate_annual` for the whole tenor. In India, an SLB borrow — naked short selling is prohibited and delivery must be honoured at settlement. Institutional accounts additionally cannot square off intra-day. |

The engine appends the located-borrow warning to `audit_notes` on every reverse signal.

## 5. Handle settlement at expiry

`physical_delivery_at_expiry` is derived from `settlement_type`, not assumed from the
venue.

- **Physically settled** (all NSE stock futures since the October 2019 expiry; some
  Eurex contracts): an unclosed leg becomes a delivery obligation for the **full
  notional** — purchase consideration on the long side, deliverable shares on the short.
  Plan the roll or the unwind before the cum/ex and expiry dates, and size the position
  against the delivery obligation rather than the margin.
- **Cash settled** (CME 2026 contracts; some Eurex contracts): expiry settles to a cash
  difference against the final settlement price.

Rolling a physically settled cash-and-carry to the next expiry replaces a delivery
obligation with a new basis exposure at the roll spread. That spread is a cost the
screening threshold in step 4 does not include.

## 6. Apply the ex-dividend contract adjustment — only if it is triggered

`calculate_ex_dividend_price_adjustment` implements the SEBI/NSE test:

1. Compute the dividend as a percentage of `underlying_market_price` — the closing price
   on the day before the dividend announcement. This argument is **required**; without
   it the classification cannot be made, and v1.0.0's behaviour was to assume every
   dividend was extraordinary.
2. **Below the threshold (2% under SEBI)**: the dividend is *ordinary*. The exchange
   makes **no adjustment**. `is_adjusted` is `False` and `adjusted_base_price` equals the
   input price. The drop is absorbed by the market price.
3. **At or above the threshold**: the dividend is *extraordinary*. The futures base price
   becomes the previous **mark-to-market settlement price of the contract** less the
   aggregate dividend — the reference rate is the futures settlement, not the spot.
4. Adjustments are applied on the last cum-basis trading day, after the close.

Pass `extraordinary_threshold_pct` explicitly when trading a venue with a different
rule. The 2% figure is Indian and replaced a 5% threshold on 28 June 2022 — a backtest
spanning that date needs both.

## 7. Interpret the margin comparison

`ssf_margin_pct` and `spot_margin_pct` default only for venues in `FLAT_MARGIN_VENUES`
(currently CME, from the US statutory minimums: 15% security futures, 50% Reg T).
For NSE, Eurex and Euronext the call raises `SSFConfigError`: those venues margin
scenario-wise (NSE Clearing SPAN plus a 3.5% ELM on stock futures; Eurex Clearing
Prisma) and no flat percentage of notional reproduces their requirement.

`leverage_multiplier = spot_margin_pct / ssf_margin_pct` is that ratio and nothing more.
It is not capital efficiency: the futures leg marks to market daily and can call
variation margin the spot leg would not, and on a physically settled contract the
expiry obligation is the full notional regardless of the margin posted.

`margin_basis` records where the percentages came from, so a defaulted figure is never
mistaken downstream for a measured one.
