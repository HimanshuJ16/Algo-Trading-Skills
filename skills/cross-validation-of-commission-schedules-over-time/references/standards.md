# Standards — cross-validation-of-commission-schedules-over-time

## Scope and jurisdiction

Everything below is **US cash equities**. Commission schedules are broker-specific
and non-transferable: a schedule sourced from one broker must not be applied to a
backtest executed against another. Non-US venues carry entirely different fee
architectures (stamp duty, exchange transaction levies, STT) that this module does
not model.

## Broker commission schedule — worked reference (Charles Schwab retail online US equity/ETF)

This is the schedule shipped as `DEFAULT_SCHWAB_RETAIL_SCHEDULE`. It is a
*reference example* to illustrate the shape of a real time-varying schedule, not a
universal fee table.

| Effective (inclusive) | Standard online equity/ETF commission | Structure | Sourcing |
|---|---|---|---|
| ? – 2017-02-02 | $8.95 per trade | flat ticket | rate sourced; **start date unverified** |
| 2017-02-03 – 2017-03-02 | $6.95 per trade | flat ticket | effective 2017-02-03 |
| 2017-03-03 – 2019-10-06 | $4.95 per trade | flat ticket | effective 2017-03-03 |
| 2019-10-07 – present | $0.00 | flat ticket | effective 2019-10-07 |

Notes on this table, because they matter for correctness:

- The rate changed **three times in 32 months**. Any backtest spanning 2016–2020
  that applies a single commission figure is wrong for most of its sample.
- Schwab's structure is a **flat per-ticket** fee with **no per-share component**.
  Modelling it as "ticket fee + per-share fee" describes no real broker.
- The start of the $8.95 era is *not* established by the cited sources. The shipped
  tier uses `2010-01-01` as a placeholder floor and flags this in its `source`
  field. Verify it against the broker's own historical pricing disclosures before
  backtesting periods before 2017-02-03.
- Zero-commission cutover dates differ **by broker**: TD Ameritrade went to $0
  effective 2019-10-03, Schwab and E\*TRADE effective 2019-10-07 (E\*TRADE from
  $6.95). Using one broker's cutover date for another mis-costs the intervening days.
- Zero-commission is a **US retail** phenomenon. Interactive Brokers Pro clients on
  Fixed pricing continued to pay $0.005/share (minimum $1.00 per order, maximum 1%
  of trade value) throughout — the structure exported as
  `IBKR_FIXED_US_EQUITY_TIER`, and the reason the tier model carries both a floor
  and a percent-of-value cap.

Sources: [Schwab press release, 2017-02-28 (reduction to $4.95)](https://pressroom.aboutschwab.com/press-releases/press-release/2017/Schwab-Reduces-Trade-Commissions-to-4.95-and-Lowers-Per-Contract-Options-Fee-to-0.65/default.aspx),
[CNN Money, 2017-02-28 ($8.95 → $6.95 from Feb 3, then → $4.95)](https://money.cnn.com/2017/02/28/pf/schwab-lowers-trade-commissions/index.html),
[CNBC, 2019-10-01 (Schwab to $0 effective Oct 7)](https://www.cnbc.com/2019/10/01/charles-schwab-is-eliminating-online-commissions-for-trading-in-us-stocks-and-etfs.html),
[Interactive Brokers — Stocks commissions](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php).

## Regulatory pass-through fees — "zero commission" is not zero cost

Both charges below apply to **sales only**, are passed through to the selling
customer, and change on regulator-published effective dates. A backtest that
charges $0.00 to a post-2019 sell is under-costing every exit.

| Charge | Basis | Assessed on | Cap |
|---|---|---|---|
| SEC Section 31 fee | dollars per $1,000,000 of covered-sale proceeds | sales | none |
| FINRA Trading Activity Fee (TAF) | per share | sales of covered equity securities | per-trade maximum |

Verified rate points (illustrating that the rate genuinely moves, including to zero):

| Charge | Rate | Effective |
|---|---|---|
| Section 31 | $22.40 per million | 2012-04-01 (FY2012 mid-year adjustment) |
| Section 31 | $27.80 per million | through 2025-05-13 |
| Section 31 | $0.00 per million | from 2025-05-14 |
| Section 31 | $20.60 per million | from 2026-04-04 |
| FINRA TAF | $0.000166 per share, max $8.30 per trade | current rate per FINRA Schedule A |

This module ships **no default regulatory history** — populating one would mean
inventing rates for dates not verified here. Build a `RegulatoryFeeTier` list
covering your backtest range from the primary sources below. When
`regulatory_schedule` is left as `None`, results are flagged
`regulatory_fees_modeled=False` so a report can never imply these costs were
measured and found to be zero.

Sources: [SEC Section 31 fee-rate advisories](https://www.sec.gov/rules-regulations/fee-rate-advisories),
[SEC Section 31 transaction fees — basic information for firms](https://www.sec.gov/rules-regulations/fee-rate-advisories/section-31-fees-basic-information-firms),
[FINRA By-Laws Schedule A, Section 1 — Member Regulatory Fees](https://www.finra.org/rules-guidance/rulebooks/corporate-organization/section-1-member-regulatory-fees)
("Each member shall pay to FINRA a fee per share for each sale of a covered equity
security ... $0.000166 per share ... $8.30 per trade"),
[FINRA Trading Activity Fee guidance](https://www.finra.org/rules-guidance/guidance/trading-activity-fee).

## Regulatory & Operational Notes

Section 31 and the FINRA TAF are **mandatory** charges on covered US equity sales;
the rates above are regulator-published, not engineering defaults. The broker
commission table, by contrast, is a commercial schedule and carries no regulatory
force — it must be replaced with your own broker's published historical pricing.
Neither the SEC nor FINRA prescribes how a backtest models transaction costs, but
materially understating costs in a backtest used to market a strategy raises
separate performance-advertising concerns under the relevant securities regime
(e.g. US SEC marketing rules) that are out of scope here.
