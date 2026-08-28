# Workflows for Short-Selling Borrow Cost and Availability Modeling

## 1. Register borrow status before anything else

`BorrowStatus(ticker, utilization_rate, available_shares, observed_borrow_rate=None)`.

- `utilization_rate` is on-loan quantity over lendable inventory, validated to $[0, 1]$.
  A value above 1.0 is corrupt data, not an extreme borrow signal, and is rejected.
- `available_shares` is what your lender will actually offer. It is required — there is
  no safe default inventory number.
- `observed_borrow_rate` is the quoted annualized rate. Supply it whenever you have it.

Nothing else in the module works on an unregistered ticker: availability returns
`NO_BORROW_STATUS` and every pricing call raises `UnknownBorrowStatusError`.

## 2. Availability gate

```
result = modeler.check_availability("GME", 5_000)
if not result.is_available:
    reject_order(reason=result.reason)
```

Reason codes, in evaluation order:

| Reason | Meaning |
|---|---|
| `NO_BORROW_STATUS` | Ticker never registered. Fail closed. |
| `NO_INVENTORY` | `available_shares <= 0`. |
| `INSUFFICIENT_INVENTORY` | Request exceeds offered inventory. |
| `FULLY_UTILIZED` | Inventory reported while utilization is 100% — contradictory data, refused. |
| `AVAILABLE` | Inventory looks sufficient. |

This is an inventory check. It is not a Regulation SHO locate, it does not reserve
anything, and it does not survive a recall.

## 3. Rate resolution

```
rate, source = modeler.resolve_rate("GME")
```

- `observed` — the quoted rate on the status. Always preferred.
- `heuristic_gc` — utilization at or below `htb_utilization_threshold`; flat `gc_rate`.
- `heuristic_htb` — linear ramp from `htb_base_rate` at the threshold to `max_htb_rate`
  at 100% utilization.

Log or persist `rate_source` alongside any cost number. A cost derived from
`heuristic_htb` is an interpolation over a supply metric, and a research result that
depends on it depends on an invented curve.

The ramp is discontinuous at the threshold: on the defaults a name at 0.800 utilization
prices at 0.30% and a name at 0.801 prices at 5.00%. That step is an artifact of the
piecewise model. Do not build a signal on it, and do not use the boundary as a
classification cutoff for anything that matters.

## 4. Fee accrual

Collateral base (MSLA Sec. 9; IBKR's documented US convention):

$$\text{Collateral} = \text{shares} \times \text{price} \times \text{MarginPct}$$

with the margined per-share price optionally rounded up to the whole dollar
(`round_collateral_price_up=True` reproduces IBKR statements).

Daily fee:

$$\text{DailyFee} = \text{Collateral} \times \frac{\text{Rate}}{\text{DayCountBasis}}$$

`DayCountBasis` is 360 for USD- and EUR-denominated loans and 365 for sterling. Accrual
runs from and including the open date to but excluding the cover date, on calendar days
— weekends accrue.

Two entry points:

- `calculate_borrow_cost(trade)` / `calculate_borrow_cost_detail(trade)` — one rate, one
  price, whole period. Fast, and an approximation that understates a short moving
  against you.
- `calculate_borrow_cost_schedule(ticker, shares, daily_marks, daily_rates=None)` —
  accrues on each day's mark and, optionally, each day's rate. This is the convention
  the fee is actually charged under and the correct path for a backtest.

**Look-ahead:** `daily_marks[i]` must be the settlement price established *before* day
`i`'s accrual — IBKR accrues on the prior day's settlement price. Passing day `i`'s own
close charges the position against a price it did not yet know.

## 5. Net financing

`gross_borrow_cost_usd` is the fee. Where the account is credited interest on short sale
proceeds, set `short_proceeds_credit_rate` and read `net_financing_cost_usd`, which can
be negative in a high-rate environment on a GC name. The credit accrues on bare proceeds
(shares × mark), not on the margined collateral; broker-specific tiers and
minimum-balance thresholds are out of scope.

Where collateral is cash, the market equivalent of this netting is the rebate rate: the
lender pays a Cash Collateral Fee on the cash and the borrow fee is embedded in it
(MSLA Sec. 5.1). A "negative rebate" is the same economics as a fee exceeding the
interest earned on collateral.

## 6. Recall and squeeze triage

```
assessment = modeler.assess_recall_risk("GME")
```

| Tier | Trigger | Meaning |
|---|---|---|
| `HIGH` | Utilization 100%, or no inventory offered | A recall is unlikely to be replaceable; the realistic outcome is a buy-in. |
| `ELEVATED` | Utilization ≥ `recall_watch_utilization` (default 0.90) | Thin supply; rates can reprice sharply. |
| `LOW` | Below the watch level | Headroom in the lendable pool. |

Under MSLA Sec. 6.1(a) either party may terminate an open loan on notice, with the
termination date no earlier than standard settlement. A short position is therefore
never term-funded by default, and the tiers above are review triggers rather than
probabilities — no public source publishes a calibrated recall model.

## 7. Backtest integration checklist

1. Point-in-time borrow data only. Using today's utilization and rate history to price a
   2019 short is look-ahead in the cost line.
2. Reject, do not default. A universe member with no borrow record on a given date is
   not shortable that date.
3. Deduct `net_financing_cost_usd` from short P&L each day, not once at exit — a
   position closed early accrues fewer days, and per-day accrual is what makes holding
   period sensitivity visible.
4. Persist `rate_source` per position-day so heuristic-priced and quote-priced results
   can be separated in the tearsheet.
5. Re-check availability and recall tier on every rebalance, not only at entry.
