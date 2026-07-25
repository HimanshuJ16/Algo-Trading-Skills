# Margin Interest and Borrow Fee Tracking Workflows

## 1. Daily EOD Balances Extraction
At 17:00 ET (or equivalent market close), capture the exact cash debit balance and the gross short market value of the portfolio. Intraday leverage does not incur overnight interest; only EOD balances matter.

## 2. Progressive Rate Calculation (Blended)
For brokers like Interactive Brokers, margin interest is computed progressively.
- E.g., for a $300k debit balance:
  - The first $100k is charged at Tier 1 (e.g., 6.83%).
  - The remaining $200k is charged at Tier 2 (e.g., 6.33%).
- Derive the `effective_APR = (100k * 6.83% + 200k * 6.33%) / 300k = 6.496%`.

## 3. Daily Accrual Formula
Calculate the daily margin charge:
`Daily_Margin_Cost = Debit_Balance * (effective_APR / 360)`
Note the 360-day divisor for USD.

Calculate the daily short borrow fee:
`Daily_Borrow_Cost = Short_Market_Value * (HTB_APR / 360)`

## 4. Weekend and Holiday Adjustments
- **Fridays**: A position held overnight on Friday accrues 3 days of interest (covering Friday, Saturday, and Sunday) since settlement cannot occur until the next business day.
- **Holidays**: If Monday is a market holiday, positions held from Friday accrue 4 days of interest. Incorporate a trading calendar to track non-settlement days dynamically.

## 5. Ledger Integration
Append the daily total cost (`Daily_Margin_Cost + Daily_Borrow_Cost`) to the strategy's continuous ledger.
- `Net_PnL = Gross_Trading_PnL - Cumulative_Cost`
- Perform this at the portfolio level (for cash balance) and at the position level (for specific short borrow fees).
