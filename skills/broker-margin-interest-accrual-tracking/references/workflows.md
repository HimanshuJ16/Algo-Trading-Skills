# Margin Interest and Borrow Fee Tracking Workflows

## 1. Build the rate schedule

Pull your broker's **current** published table. IBKR quotes tiers as benchmark + spread,
so an absolute schedule copied from a screenshot decays as soon as policy moves.

```python
tiers = tiers_from_benchmark(
    benchmark_apr=0.0433,                                   # today's Fed Funds effective
    spreads=[(100_000, 0.015), (float("inf"), 0.005)],      # your broker's published spreads
)
tracker = MarginInterestTracker(rate_tiers=tiers, day_count_convention_margin=360)
```

The schedule is validated on construction. It must start at zero, be contiguous, and end
in an **open-ended** top bracket. A capped top bracket prices everything above the cap at
0%: a schedule ending at $100k reports a 2.5% effective rate on a $200k balance instead of
5%, understating the loan by half with no error raised.

Set `is_blended_rate=False` only if your broker actually reprices the whole balance when
you cross a threshold. Set `short_collateral_markup=1.02` to charge borrow fees on
collateral rather than raw market value.

## 2. Capture end-of-day balances

At the market close, record the cash debit balance and gross short market value per date.
Intraday leverage that is closed before the close never accrues overnight financing and
does not belong in the schedule.

Record one `EodBalance` per observation. Observations on business days only are fine —
gaps are handled in step 3.

## 3. Accrue from dates, not day counts

```python
summary = tracker.accrue_daily_balances(balances, through_date=date_position_cleared)
```

Each observation accrues from its own date to the next observation's date; the last runs
to `through_date` (exclusive). Weekends and holidays need no special handling: observe
Friday and then Monday, and Friday's row carries three days automatically.

This is the entry point to prefer, because it derives the day count from the dates and
cannot be handed a trading-day count by mistake. Use
`calculate_interest_accrual(start_date, holding_days, ...)` only for a constant balance,
and read `holding_days` strictly as **calendar** days.

Under a blended schedule the effective APR is recomputed for every observation. This is
not a refinement — the tiered rate is not linear in the balance, so accruing on an average
balance gives the wrong answer. A day at $50k (5%) followed by a day at $200k (4.5%) costs
$31.94; the same two days accrued on the $125k average cost $33.33.

## 4. Weekend and holiday adjustments

- **Fridays**: a position carried over Friday's close accrues three days — Friday,
  Saturday, Sunday — because the balance still exists on the weekend, not because
  settlement is delayed.
- **Holidays**: `tracker.add_holidays([...])` makes the Friday before a holiday Monday
  carry a single four-day block rather than a three-day block plus a row dated on a closed
  market. This aligns the ledger with the broker's, line for line.
- **Neither changes the total.** Cost over a window is
  `balance x effective_APR / day_count x calendar_days`, whatever the start weekday.
  Verify this: the same balance accrued for 14 days from a Monday and from a Friday must
  produce identical totals. If yours differ, something is double-counting.

## 5. Daily accrual formulas

```
Daily_Margin_Cost = Debit_Balance x (effective_APR / day_count_margin)
Daily_Borrow_Cost = Collateral    x (Borrow_APR   / day_count_borrow)
```

`day_count` is 360 for USD and most currencies at IBKR, 365 for exceptions such as GBP.

`Collateral` is not market value. At IBKR it is 102% of the prior day's settlement price
rounded up to the next whole dollar, times shares. `short_collateral_markup=1.02`
approximates it; the per-share round-up needs prices this module does not take, so pass
`EodBalance.short_collateral_usd` when you need a statement-exact figure.

A **credit** cash balance produces no margin interest here. It earns interest under a
separate tiered, threshold-gated schedule that this module does not model — netting it
against borrow fees would invent income.

## 6. Ledger integration

Append each block's `total_daily_cost_usd` to the strategy ledger and report
`adjusted_net_pnl_usd = gross_pnl_usd - margin_interest - borrow_fees` alongside gross,
never in place of it. The gap between the two is what tells you whether the leverage paid
for itself.

Do this at both levels: portfolio-level for the cash debit, position-level for
per-security borrow fees.

## 7. Reconcile before trusting

Compare a full month of output against the broker's posted interest — IBKR posts the
month's accrual on the third business day of the following month. A systematic gap is
diagnostic:

| Symptom | Likely cause |
|---|---|
| Accrual ~1.4% low | 365 divisor where the broker uses 360 |
| Borrow fee ~2%+ low | Charging market value instead of 102% collateral |
| Accrual ~30% low | Trading-day count fed into a calendar-day model |
| Accrual high on large balances | Flat rate assumed where the schedule is blended |
| Drift growing over months | Simple accrual vs the broker's monthly posting/compounding |
| Short cost overstated | Rebate on short sale proceeds not credited (not modelled here) |
