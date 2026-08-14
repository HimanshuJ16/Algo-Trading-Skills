# Margin and Financing: Conventions and Sources

Every figure below is scoped to a jurisdiction or a named broker. Financing conventions
are contractual, not regulatory — they come from the broker's agreement and the relevant
market's securities-lending practice, so they differ between venues and change over time.
Verify against your own broker's current documentation before relying on any of it.

## 1. Day-Count Conventions

- **Interactive Brokers**: most currencies accrue on a **360-day** year; exceptional
  currencies (GBP is the example IBKR gives) use **365**. USD margin interest is 360.
- **US stock borrow fees (IBKR)**: the fee formula is `(Value x Fee Rate) / 360`.

Using a 365 divisor where the broker uses 360 understates the charge by
`1 - 360/365 ≈ 1.4%`. It is small per day and permanent, and it guarantees the accrual
never reconciles against the statement.

## 2. Blended vs Flat Tiers

Interactive Brokers prices margin loans in **blended (progressive)** tiers: for a balance
over USD 1,000,000, the first 100,000 is charged at the Tier I rate, the next 900,000 at
the Tier II rate, and so on. The effective rate is the balance-weighted average and falls
as the balance grows.

Some brokers instead use **flat** tiers, where crossing a threshold reprices the entire
balance. Assuming the wrong one misprices in whichever direction the schedule bends, so
confirm which your broker uses rather than defaulting to either.

**Rates are quoted as a spread over a benchmark** (Fed Funds effective for USD at IBKR),
which is why absolute APRs go stale. The IBKR Pro USD Tier I rate was near 6.8% in the
2023-24 environment and near 5.1% in 2026; the *spreads* — BM + 1.5% on the first
100,000, narrowing to BM + 0.5% at the largest balances — move far less than the rates
do. Store spreads in config and apply today's benchmark.

## 3. Short Borrow vs Margin Interest

These are separate charges with separate bases, and a position can incur one, the other,
or both.

- **Short borrow fee** — the cost of borrowing the shares. At IBKR it is charged on
  **collateral**, computed as 102% of the prior day's settlement price *rounded up to the
  next whole dollar*, times shares borrowed — not on raw market value. The 102% figure
  follows the standard initial-collateral level for US securities loans under the SIFMA
  Master Securities Loan Agreement framework (105% is typical for non-US shares), with
  collateral marked to market daily.
- **Margin interest** — charged on a negative cash balance. A short sale generates cash,
  so shorting alone need not create a debit; it does so only if total account cash goes
  into deficit.
- **Short sale proceeds interest (the rebate)** — cash proceeds from a short can earn
  interest, and the economic cost of the short is the borrow fee *net* of that rebate.
  The rebate goes negative when the borrow rate exceeds the proceeds rate, which is the
  normal state for hard-to-borrow names. A model that reports only the gross borrow fee
  overstates the cost of general-collateral shorts.

## 4. Settlement Timing and Why Weekends Cost Three Days

For US securities, the standard settlement cycle has been **T+1 since 28 May 2024**, when
the SEC's amendments to Exchange Act Rule 15c6-1 became operative (it was T+2 from
September 2017). Other jurisdictions differ; do not assume T+1 globally.

Settlement timing is **not** the reason a Friday position accrues three days of interest.
Interest is computed on the daily balance, and the balance still exists on Saturday and
Sunday — so three calendar days accrue. The same logic covers holidays: a Friday before a
holiday Monday accrues four days.

The practical consequence is that **financing cost depends only on the number of calendar
days in the window**. Grouping weekend days into Friday's ledger row is a presentation
choice that affects granularity, not totals. Two errors follow from missing this: applying
a weekend multiplier on top of a calendar-day count (double-charging), and feeding a
trading-day count into a calendar-day model (under-charging by roughly 2/7).

## 5. Accrual and Posting

IBKR accrues interest daily and posts the month's accrual as a single entry on the **third
business day of the following month**. Accrued-but-unposted interest does not affect the
settled cash balance and cannot be withdrawn.

Because interest is posted rather than capitalised daily, compounding is **monthly, not
daily**. Simple daily accrual is a close approximation over weeks; over multi-year holds
at high rates, model the monthly posting into the cash balance.

## Sources

- Interactive Brokers, *Borrow Fee Details* — fee formula `(Value x Fee Rate)/360`,
  collateral at 102% of prior day's settlement price rounded up to the nearest whole
  dollar. https://www.ibkrguides.com/reportingreference/reportguide/borrowfeedetails.htm
- Interactive Brokers, *Interest Benchmark Definitions* — most currencies on a 360-day
  year, exceptional currencies (e.g. GBP) on 365. https://www.ibkrguides.com/kb/article-974.htm
- Interactive Brokers, *Margin Interest Calculations* / *Margin Rates and Financing* —
  blended tier mechanics, benchmark-linked rates, daily accrual with monthly posting.
  https://www.interactivebrokers.com/en/trading/margin-calculation-details.php and
  https://www.interactivebrokers.com/en/trading/margin-rates.php
- SEC, amendments to Exchange Act Rule 15c6-1 (adopted 15 February 2023, compliance date
  28 May 2024) shortening the standard US settlement cycle to T+1; see also FINRA
  Regulatory Notice 24-04. https://www.finra.org/rules-guidance/notices/24-04
- SIFMA, *Overview of the Master Securities Loan Agreement* — 102% initial collateral for
  US securities loans, daily mark-to-market.
  https://www.sifma.org/wp-content/uploads/2017/08/MSLA_Overview-of-the-Master-Securities-Loan-Agreement-1993-Version.pdf
