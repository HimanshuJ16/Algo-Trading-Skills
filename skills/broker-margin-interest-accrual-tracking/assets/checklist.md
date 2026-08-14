# Pre-Flight Checklist: Margin & Financing Integration

## Rate schedule
- [ ] **Currency**: Are the broker's tiers pulled from the **current** published table, not
      a stored snapshot? (Rates are benchmark-linked and drift with policy.)
- [ ] **Top bracket open-ended**: Does the highest tier end at infinity? A capped top tier
      prices the excess at 0% and halves the reported cost of large loans.
- [ ] **Blended vs flat**: Confirmed which one the broker actually uses, rather than
      assumed?
- [ ] **Day-count**: 360 for USD financing (365 only for currencies the broker lists as
      exceptions, e.g. GBP)?

## Balances
- [ ] **End-of-day only**: Are intraday positions closed before the close excluded?
- [ ] **Per-day, not averaged**: Is the balance recorded per date? Tiered rates are not
      linear in the balance, so an average balance gives the wrong effective APR.
- [ ] **Credit balances not netted**: Does a positive cash balance produce zero margin
      interest rather than a negative charge offsetting borrow fees?

## Calendar
- [ ] **Calendar days, not trading days**: Is the day count derived from dates? A
      trading-day count understates financing by roughly 30% over a year of carry.
- [ ] **No weekend multiplier on top**: Confirmed that Friday→Monday is charged three days
      once, not three days on top of an already-correct calendar count?
- [ ] **Start-weekday invariance**: Does the same balance held 14 days from a Monday cost
      exactly what it costs from a Friday?
- [ ] **Holiday calendar loaded**: Are exchange holidays registered so accrual blocks align
      with settlement days and no ledger row is dated on a closed market? (Affects
      reconciliation, not the total.)

## Short book
- [ ] **Collateral basis**: Is the borrow fee charged on collateral (IBKR: 102% of prior
      settlement price rounded up to the next whole dollar × shares), not raw market value?
- [ ] **Borrow rates current**: Are per-security rates refreshed daily rather than held
      constant across the backtest?
- [ ] **Rebate treated as unmodelled**: Is it understood that the output is the *gross*
      borrow fee, and therefore an upper bound on short financing cost?

## Failure handling
- [ ] **Fails closed**: Does a NaN or infinite balance raise rather than silently zeroing
      the financing cost or poisoning net P&L?
- [ ] **Schedule errors raise**: Does a malformed tier table raise at construction rather
      than returning a plausible-looking rate?

## Reconciliation and reporting
- [ ] **Monthly reconciliation**: Has a full month been compared against the broker's
      posted interest (IBKR: third business day of the following month)?
- [ ] **Compounding gap understood**: For multi-year holds, is the monthly posting modelled
      rather than relying on simple daily accrual?
- [ ] **Gross reported alongside net**: Is `adjusted_net_pnl_usd` shown next to gross P&L,
      so the cost of leverage is visible rather than buried?
