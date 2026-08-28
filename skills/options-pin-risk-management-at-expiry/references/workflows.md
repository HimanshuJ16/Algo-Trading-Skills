# Workflows for Options Pin Risk Management at Expiry

The procedure below runs on the expiration date, against a book of positions
that are still tradeable. Timings are US listed equity options; see
`standards.md` for sources.

## 0. Establish the clock before anything else

Three deadlines, in the order they bite:

1. **Trading in the option ceases** — 4:00 p.m. ET for standard equity options.
   The last moment a position can be closed in the market.
2. **Your broker's exercise cutoff** — earlier than the regulatory ceiling, set
   by the member, and the one that actually binds a customer. Look it up; do not
   assume a number.
3. **The regulatory ceiling** — 5:30 p.m. ET, FINRA Rule 2360(b)(23)(A). Members
   may not accept instructions after it.

The contract's expiration time, 11:59 p.m. ET, is not the deadline for anything
you can do. Feed the engine `hours_to_trading_close`, measured against (1).

## 1. Pin zone detection

- $\text{PinDistance}_{\$} = |S - K|$, $\text{PinDistance}_{\%} = \frac{|S-K|}{S} \times 100$.
- In the pin zone when the distance is inside the band **and**
  $\text{HoursToTradingClose} \le \text{PinCutoffHours}$.
- The percentage band alone scales badly at both ends of the price range. Set
  `pin_distance_abs_usd` to add an absolute band (applied with OR) on low-priced
  underlyings, or tighten `pin_distance_pct` on high-priced ones.
- Distance is rounded to six decimals before the comparison, so the number the
  report publishes is the number the decision was made on.

## 2. Signed moneyness against the exercise-by-exception threshold

- $\text{Moneyness}_{\text{call}} = S - K$; $\text{Moneyness}_{\text{put}} = K - S$.
- $\text{Moneyness} \ge \$0.01 \implies$ default outcome `AUTO_EXERCISED` under
  OCC Rule 805; otherwise `EXPIRES_WORTHLESS`.
- Round before comparing. $1234.01 - 1234.00$ is `0.009999999999990905` in binary
  floating point, so a raw comparison misclassifies the exact boundary.
- `spot_price` should be the **official close** — OCC applies the test to the
  close. Before it is known, the verdict is provisional and flagged.

## 3. Direction-of-surprise resolution

| Side | Moneyness at close | Default outcome | What can go wrong | Action |
|---|---|---|---|---|
| Short | ITM $\ge \$0.01$ | Assigned | Holder files contrary advice to **cancel**; a pre-bought hedge becomes an outright long | `CLOSE_POSITION_BEFORE_EXPIRY` |
| Short | OTM | Abandoned | Holder files contrary advice to **exercise**; unhedged share position appears | `CLOSE_POSITION_BEFORE_EXPIRY` |
| Long | ITM $\ge \$0.01$ | Exercised | Unwanted or unfunded delivery; a DNE forfeits intrinsic | `CLOSE_POSITION_BEFORE_EXPIRY`, `dne_eligible` set |
| Long | OTM | Abandoned | Nothing — the holder elects | `NO_ACTION_HOLDER_ELECTS` |
| Any | — | — | Trading already closed | `POST_CLOSE_EXPOSURE_REVIEW` |
| Cash-settled | — | Cash settlement | No share delivery to be uncertain about | `REVIEW_CASH_SETTLEMENT_EXPOSURE` |

Rolling is an equivalent operator response wherever `CLOSE_POSITION_BEFORE_EXPIRY`
is emitted: what matters is that no pinned short survives the close.

## 4. Exposure quantification

- $\text{ShareDelta} = \pm |Q| \times \text{Multiplier}$, signed by delivery
  direction: short call and long put deliver shares away (negative), short put
  and long call receive them (positive). Cash-settled contracts are zero.
- $\text{ShareNotional} = |\text{ShareDelta}| \times S$ — market value of the
  shares that move.
- $\text{AssignmentCash} = |\text{ShareDelta}| \times K$ — cash exchanged on
  exercise. **Size margin and funding on this one.** For an assigned short put,
  spot understates the obligation exactly when the put is in the money.
- For a DNE-eligible long, $\text{IntrinsicForfeited} = \text{Moneyness} \times
  |Q| \times \text{Multiplier}$ makes the abandon-versus-deliver trade-off
  explicit — often a few dollars of intrinsic against a five- or six-figure
  weekend share position.

## 5. Portfolio netting per underlying

Run `audit_portfolio_pin_risk` over the whole expiring book. Per underlying:

- **Certain delta** — the sum of share deltas from positions *outside* the pin
  zone whose default outcome is `AUTO_EXERCISED`.
- **Range** — $[\text{certain} + \sum \min(d, 0),\ \text{certain} + \sum \max(d, 0)]$
  over the pinned positions' deltas $d$. This is the literal statement of pin
  risk: the set of share positions the book can wake up to.
- **Unpaired short legs** — pinned short *shares* of a given type minus the
  shares deliverable by long legs of the same type that are in the money beyond
  \$0.01 *and* outside the pin band. Only those longs deliver, so only those
  cover. A long leg that is merely "part of the spread" but out of the money
  hedges nothing. The netting is in shares, not contracts: 10 short contracts of
  100 against 10 long contracts of 10 leaves 900 naked shares.
- **Inconsistent spot prices** across legs of one underlying are flagged; they
  mean at least one leg is being scored off a stale mark.

Status is `UNPAIRED_SHORT_PIN_EXPOSURE`, `PINNED_SHORT_DELIVERY_COVERED`, or
`NO_PIN_EXPOSURE`. Coverage is not the same as a known position: with a covered
spread the account still ends up either flat or long the delivered shares, so
read the range rather than treating the exposure as zero.

## 6. Audit report generation

Persist the full per-position and per-underlying report for every run, including
`data_quality_flags`. Post-assignment forensics on the following Monday depends
on knowing what the desk saw and when it saw it.
