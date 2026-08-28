# Standards for Short-Selling Borrow Cost and Availability Modeling

Everything below is either a documented contractual/market convention with a source, or
is explicitly labelled as a model choice. Nothing in this file is a regulatory
requirement — the regulatory side of short selling lives in
`us-reg-sho-short-sale-locate-requirements` and
`eu-short-selling-regulation-disclosure-thresholds`.

## Documented conventions

| Convention | Value | Source |
|---|---|---|
| Loan Fee accrual frequency and base | "computed daily on each Loan … based on the aggregate Market Value of the Loaned Securities on the day for which such Loan Fee is being computed" | SIFMA 2017 Master Securities Loan Agreement, Sec. 5.1 |
| Accrual period | "from and including the date on which the Loaned Securities are transferred to Borrower to, but excluding, the date on which such Loaned Securities are returned" — calendar days | MSLA 2017, Sec. 5.1 |
| Negative fees | A Loan Fee may be "less than zero", in which case the Lender pays the Borrower | MSLA 2017, Sec. 5.1 |
| Cash-collateral leg | Where collateral is cash, the economics run through a Cash Collateral Fee (rebate) computed daily on the cash held | MSLA 2017, Sec. 5.1 |
| Daily mark to market | Borrower marks each Loan daily; collateral shortfalls are topped up to the Margin Percentage | MSLA 2017, Sec. 9.1–9.3 |
| Margin Percentage | At least 100% of the Market Value of Loaned Securities; the specific percentage is agreed between the parties | MSLA 2017, Sec. 4.1, 9.2 |
| US market practice markup | "a deposit equal to 102% of the prior day's settlement price, rounded up to the nearest whole dollar and then multiplied times the number of shares borrowed" | IBKR reporting reference, *Non-Direct Hard to Borrow Details* |
| Fee formula and day count | "(Value x Fee Rate)/360" | IBKR reporting reference, *Non-Direct Hard to Borrow Details* |
| Day count by currency | USD and EUR money-market instruments accrue ACT/360; sterling accrues ACT/365 fixed | London interbank money-market convention |
| Open-term recall | "either party may terminate a Loan on a termination date established by notice … no earlier than the standard settlement date that would apply to a purchase or sale of the Loaned Securities" | MSLA 2017, Sec. 6.1(a) |
| Utilization | On-loan value (or quantity) divided by total gross lendable inventory of the security | DataLend, *Securities Lending 101: Understanding Market Metrics* |
| Fee/borrow rate | "The rate charged by a lender to a counterpart for borrowing securities" — "the more difficult it is to borrow, the higher the fee" | DataLend, ibid. |

## Model choices — NOT standards

These are the numbers this module ships as defaults. No public source defines them, and
they must be recalibrated against the desk's own borrow history before being used to
size or reject trades.

| Parameter | Default | Status |
|---|---|---|
| `htb_utilization_threshold` | 0.80 | Operational triage threshold chosen for this model. No regulator, exchange, or data vendor publishes an HTB utilization cutoff. |
| `gc_rate` | 0.003 | Placeholder standing in for a General Collateral quote. GC names lend cheaply and specials do not — that ordering is documented; the specific level is not, and it moves month to month. |
| `htb_base_rate` / `max_htb_rate` | 0.05 / 0.50 | Endpoints of a linear ramp. **The utilization → fee mapping is invented.** DataLend documents only the direction of the relationship, never a functional form. Supply `observed_borrow_rate` whenever a real quote exists. |
| `recall_watch_utilization` | 0.90 | Review trigger, not a calibrated recall probability. |
| `short_proceeds_credit_rate` | `None` | No rebate modelled by default. Conservative for cost estimation; it is not a claim that proceeds are never credited. |

## Sources

- SIFMA, *2017 Master Securities Loan Agreement* — https://www.sifma.org/wp-content/uploads/2017/06/MSLA_Master-Securities-Loan-Agreement-2017-Version.pdf (Sec. 4, 5.1, 6.1, 9)
- Interactive Brokers, *Non-Direct Hard to Borrow Details* (Activity Statement reporting reference) — https://www.ibkrguides.com/reportingreference/reportguide/borrowfeedetails_default.htm
- DataLend, *Securities Lending 101: Understanding Market Metrics* — https://datalend.com/securities-lending-101-understanding-market-metrics/
- SEC, *Regulation SHO* (17 CFR § 242.200–204) — for the locate and close-out obligations this module does **not** implement — https://www.sec.gov/rules-regulations/2004/07/short-sales
