# Standards — opportunity-cost-tracking-for-idle-capital

## Benchmark facts (verified against primary sources)

| Fact | Source |
|---|---|
| SOFR is a broad measure of the cost of borrowing cash overnight collateralized by US Treasury securities, published each business day by the Federal Reserve Bank of New York at approximately 8:00 a.m. ET. | [FRBNY — Secured Overnight Financing Rate](https://www.newyorkfed.org/markets/reference-rates/sofr) |
| The ARRC recommends an **Actual/360** day count for SOFR term rates and SOFR averages, consistent with the standard convention in US money markets. | [ARRC — *An Updated User's Guide to SOFR* (2021)](https://www.newyorkfed.org/medialibrary/Microsites/arrc/files/2021/users-guide-to-sofr2021-update.pdf) |
| The SOFR Averages and SOFR Index compound daily on an Actual/360 basis with no lookback; the Index measures the cumulative effect of compounding SOFR on a unit of investment, seeded at 1.00000000 on 2 April 2018. Compounding occurs on business days, with simple interest applied across non-business days at the preceding business day's rate. | [FRBNY — SOFR Averages and Index Data](https://www.newyorkfed.org/markets/reference-rates/sofr-averages-and-index) |
| Actual/360 is the day count for interest accrued on US Treasury bills and other money-market instruments. Actual/365 (actual year) applies to coupon Treasury yield quotes; SONIA uses Actual/365. | [ACT Wiki — Day count conventions](http://wiki.treasurers.org/wiki/Day_count_conventions) |

**Implementation impact.** Accruing a SOFR-quoted rate over `days/365` understates the
drag by `365/360 - 1 = 1.3889%`. `DayCount.ACT_360` is the engine default;
`DayCount.ACT_365F` exists for rates genuinely quoted on a 365-day year.

**Approximation disclosed.** `AccrualMethod.DAILY_COMPOUNDED` compounds at `rate/basis`
every *calendar* day. The published SOFR Index compounds on business days only, so
calendar-day compounding is a slight overestimate. For an exact realized figure, take
the ratio of two published SOFR Index values rather than modelling any accrual here.

## What is *not* a standard

No regulator or standards body publishes a mandatory idle-cash ratio, sweep threshold,
or operational buffer. Every value below is a library default and a starting point for
calibration, not a requirement to comply with. Calibrate against your own margin call
frequency, settlement cycle, and redemption timing, and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| Benchmark rate (`benchmark_rate_pct`) | **none — required** | Numerator of the drag. Deliberately has no default: a hardcoded rate accrues silently against a stale level. Pull it from the FRBNY publication for the relevant date. |
| Cash yield (`cash_yield_pct`) | $0.0\%$ | Rate the idle cash already earns. The default asserts the cash earns nothing, which is true for very few accounts. |
| Max target idle ratio | $0.05$ (i.e. $5\%$) | Alert threshold, compared with a strict `>`. Must be a **fraction**; passing `5` is rejected. |
| Min sweep threshold | $\$100{,}000$ | Operational floor applied to the **sweepable** balance, not the raw idle balance. |
| Sweep transaction cost | $\$50$ | The all-in **round-trip** cost (out and back). Charging one leg makes marginal sweeps look profitable. |
| Operational buffer | $\$0$ | Cash held back for margin calls and settlement. The default of zero asserts you need none. |
| Day count | `ACT/360` | Money-market convention matching how SOFR and T-bills are quoted. |
| Accrual method | `SIMPLE` | Simple interest. `DAILY_COMPOUNDED` approximates the SOFR Index. |
| Capital reconciliation tolerance | $\$1$ | Allowed drift between `allocated + unallocated` and `total_capital` before the audit refuses to run. |

## Known limitations

- **SOFR is a benchmark, not an achievable yield.** It measures a *borrowing* cost in
  the Treasury repo market and is not investable. Real sweep destinations (money-market
  funds, T-bill ladders, broker credit-interest programs) yield near but not equal to
  it, and carry their own fees, cut-off times, and settlement lag.
- **No liquidity or credit risk is modelled.** The engine assumes swept cash returns
  when needed. Redemption timing is the caller's responsibility and is precisely what
  `operational_buffer_usd` exists to price.
- **Single currency**, single benchmark rate, single pool.
- **Pre-tax.** Sweep yield is generally taxable income.

## Category

`Capital Allocation & Cash Sweep Optimization`
