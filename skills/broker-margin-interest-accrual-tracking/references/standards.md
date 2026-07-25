# Institutional Quant Standards: Margin and Financing

## 1. Day-Count Conventions
- **US Broker Margin**: Standard is Actual/360.
- **US Stock Borrow Fees**: Standard is Actual/360.
- **International / FX Margin**: Often Actual/365, but broker-dependent.
*Failure to use 360 days for USD financing underestimates costs by ~1.4%.*

## 2. Blended vs Flat Tiers
Retail brokers may use flat tiers (you cross a threshold, your entire balance gets the new rate). Institutional primes and brokers like IBKR use blended/progressive tiers (your balance is split into buckets, each bucket taxed at its specific tier rate). Robust accounting MUST support blended tracking.

## 3. Short Borrow vs Margin Interest Separation
- **Short Borrow Fee**: Paid on the gross market value of the short position (to borrow the shares).
- **Margin Interest**: Paid on negative cash balances. Shorting generates cash (credit), but if total portfolio cash is negative, you pay margin interest on the deficit.
These are distinct costs. A strategy might incur borrow fees without paying margin interest, or vice versa.

## 4. Settlement Timing (T+1 / T+2)
Financing charges typically follow settlement, not trade date. While modern accounting accrues it on trade date + 1, weekends always count. Friday overnight positions lock up capital for 3 calendar days.

## 5. Compounding
Most prime brokers calculate interest daily and post it to the account monthly (meaning interest on interest compounds monthly, not daily). Backtests simulating multi-year hold periods must reflect monthly compounding of the interest ledger into the cash balance.
