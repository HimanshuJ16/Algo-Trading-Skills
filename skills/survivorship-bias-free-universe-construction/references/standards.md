# Standards — survivorship-bias-free-universe-construction

## Date conventions (the off-by-one that costs a session)

There is no single "delisting date". Reconcile the vendor's field to **last date the
security traded** before loading it into `InstrumentMetadata.delisting_date`.

| Source | Field | What the date means | Conversion for this engine |
|---|---|---|---|
| CRSP | `DLSTDT` | Delisting date is set to the last date of available price data ([WRDS, CRSP stock database documentation](https://wrds-www.wharton.upenn.edu/pages/grid-items/crsp-stock-database-structure/)) | None — this is the convention |
| S&P Dow Jones Indices | index deletion effective date | Change is effective **prior to the open** of that date, so the name is already out; half-open `[add, del)` | Subtract one trading session |
| SEC Form 25 / 25-NSE | removal from listing and registration | Weeks after the last trade. Twitter: suspended before the open 2022-10-28, Form 25-NSE effective at the opening of business 2022-11-08 | Do not use |
| Exchange trading halt | suspension date | First non-trading date | Subtract one trading session |

Worked example — Twitter Inc., four defensible dates:

| Date | Event |
|---|---|
| 2022-10-27 | Merger effective; **last NYSE trade, close $53.70** |
| 2022-10-27 | Each share converted into the right to receive **$54.20 cash** |
| 2022-10-28 | Trading suspended before market open |
| 2022-11-08 | NYSE removes the class from listing and registration |

Sources: [SEC Form 25-NSE, Twitter Inc.](https://www.sec.gov/Archives/edgar/data/1418091/000087666122000890/ruleprovisionnotice.htm);
[TechCrunch, 28 Oct 2022](https://techcrunch.com/2022/10/28/twitter-will-be-delisted-from-the-new-york-stock-exchange-on-november-8/).
Note that the settlement price ($54.20) and the last close ($53.70) differ — settling a
merger at the last market print is wrong by the deal spread.

## Delisting reason codes

`DelistingReason` is deliberately coarser than CRSP's three-digit `DLSTCD`, whose
leading digit carries the same distinction. Map the vendor code before registering;
vendors disagree, and the mapping is a decision the caller must own.

| CRSP `DLSTCD` range | Meaning | Maps to |
|---|---|---|
| 100 | Still trading | `ACTIVE` |
| 200–299 | Merger / acquisition | `MERGER_ACQUISITION` |
| 300–399 | Exchange (moved to another venue) | `VOLUNTARY` (or keep the name live if the new listing is registered separately) |
| 400–490 | Liquidation | `BANKRUPTCY` or `VOLUNTARY`, per the facts |
| 500–588 | Dropped — performance-related (insufficient capital, low price, delinquent filing, insufficient market makers) | `BANKRUPTCY` or `VOLUNTARY`, per the facts |

Source: [CRSP US Stock & Indexes Database data descriptions](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_&_Indexes_Database_Data_Descriptions_Guide.pdf).

## Delisting returns (verified against primary sources)

| Fact | Source | Applied here |
|---|---|---|
| CRSP's delisting return compares the delisting amount with the price on the last day of trading; it is **missing** for most stocks delisted for negative reasons since 1962, and the omitted returns are large and negative | Shumway, T. (1997), "The Delisting Bias in CRSP Data", *Journal of Finance* 52(1), 327–340 — [author's copy](https://www.tylergshumway.org/Shumway-DelistingBiasCRSP-1997.pdf) | `delisting_return` mode exists because the terminal value is frequently unknown. Zero is not the fallback |
| Average delisting return of **−29.9%** for NYSE/AMEX over 1962–1993; −30% is the figure the literature applies to missing performance-related delisting returns | ibid. | `SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN = -0.30` |
| A corrected return of **−55%** for missing performance-related delisting returns corrects the bias in CRSP's Nasdaq data | Shumway, T. and Warther, V.A. (1999), "The Delisting Bias in CRSP's Nasdaq Data and Its Implications for the Size Effect", *Journal of Finance* 54(6) — [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/0022-1082.00192) | `SHUMWAY_WARTHER_1999_NASDAQ_DELISTING_RETURN = -0.55` |
| Imputing −30% (NYSE/AMEX) and −55% (Nasdaq) for missing performance-related delisting returns is standard practice | Beaver, W., McNichols, M. and Price, R. (2007), "Delisting returns and their effect on accounting-based market anomalies", *Journal of Accounting and Economics* 43(2–3) — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0165410106000930) | Both constants documented as **imputations for a missing value**, never overrides of an observed `DLRET` |

**These are estimates, not constants.** They come from US equity samples of that era.
Applying them to another venue, another decade, or another asset class without
re-estimation is unsupported, and the module says so at the constant.

Bankruptcy is not automatically zero. Lehman Brothers' common stock left the NYSE in
September 2008 and continued trading over the counter as `LEHMQ`; common holders were
ultimately wiped out, but a position was sellable in the interim at non-zero prices. A
`0.0` settlement is a modelling choice that must be stated, not the default truth.

## Magnitude of the bias (do not quote a single number)

| Claim | Source | Status |
|---|---|---|
| Survivorship bias "will definitely have an effect on the results, but the size of this effect is an empirical question" | Brown, S.J., Goetzmann, W., Ibbotson, R.G. and Ross, S.A. (1992), "Survivorship Bias in Performance Studies", *Review of Financial Studies* 5(4) — [copy](https://terpconnect.umd.edu/~wermers/ftpsite/FAME/Brown_Goetzmann_Ibbotson_Ross.pdf) | The defensible framing |
| Overestimation of roughly 20–80 bp/year depending on weighting scheme | Brown and Goetzmann (1995), "Performance Persistence", *Journal of Finance* 50(2) | Equity fund performance studies |
| 71–91 bp on three-factor-model alphas | Elton, Gruber and Blake (1996), "Survivorship Bias and Mutual Fund Performance", *Review of Financial Studies* 9(4) | Mutual funds |

The earlier claim that survivorship bias inflates backtested Sharpe ratios "by 20% to
50%" had no source and has been removed. Measure the effect on your own universe with
the ghost audit; a headline number transplanted from a mutual-fund study is not
evidence about your strategy.

## Regulatory and standards touchpoints

Nothing here mandates a universe-construction method. These are the provisions that
make the *disclosure* of a backtest's construction matter.

| Provision | Text / requirement | Applicability |
|---|---|---|
| GIPS Standards for Firms (2020), **3.A.9** | "Terminated portfolios must be included in the historical performance of the composite up to the last full measurement period that each portfolio was under management and for which the firm has discretion." | Mandatory for GIPS-compliant firms. The same principle as this skill, applied to *actual* composites: removing what died is the violation. |
| GIPS Standards for Firms (2020), **4.C.48** | Where a GIPS composite report includes theoretical performance as supplemental information, the firm must disclose that the results are theoretical and not based on actual assets, describe the methodology and assumptions, disclose fee treatment, and clearly label it as supplemental. | Mandatory. **A backtest is theoretical performance, not composite performance** — survivorship-bias treatment is part of the methodology that must be disclosed. |
| SEC Rule 206(4)-1(d)(6) (Marketing Rule) | Hypothetical performance — which the rule's paragraph (e) defines as results "not actually achieved by any portfolio of the investment adviser", expressly including backtested performance — may be advertised only if the adviser adopts policies ensuring relevance to the intended audience and provides sufficient information to understand the criteria, assumptions, risks and limitations. | Mandatory for SEC-registered US investment advisers. [17 CFR 275.206(4)-1](https://www.law.cornell.edu/cfr/text/17/275.206(4)-1) |

Source: [GIPS Standards for Firms 2020 (CFA Institute)](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf).

## Data provider coverage

| Provider | Point-in-time field | Delisting settlement handling |
|---|---|---|
| CRSP | `DLSTDT` (last price date), `DLSTCD` (reason), `PERMNO` (stable security id — use it as `security_id`) | `DLRET` delisting return; frequently missing for performance-related delistings — impute per the table above |
| Sharadar (US equities) | `isdelisted`, ticker `actions` table, `permaticker` | Final price snapshot on the delisting date; no delisting return |
| Norgate Data (equities / futures) | Point-in-time index constituents, delisted database | Delisted series retained with adjusted and unadjusted prices |
| QuantConnect Data | Map files (ticker→security over time), factor files | `Delisting` events in the data feed |

Any provider without a **stable security identifier** cannot express ticker recycling.
Registering such a feed by ticker collapses recycled pairs — see `add_instrument`,
which raises rather than overwriting.

## Known limitations

- **Not a data source.** Internally consistent metadata is not point-in-time metadata.
  A registry back-filled from a current-membership table audits clean and is biased.
- **Tradability, not index membership.** Use `point-in-time-index-constituent-tracking`
  for the membership axis, `backtest-look-ahead-in-universe-selection` for the
  knowledge axis.
- **The ghost check compares tickers**, so a recycled ticker hides its delisted issuer
  — old GM is not a ghost because `GM` is in today's index. Read `ghost_count` as a
  lower bound and use `delisted_in_period` alongside it.
- **No corporate-action adjustment.** `last_traded_price` must be on the same basis as
  the position quantity.
- **Cash values are IEEE-754 floats.** Adequate for backtest P&L, not for books and
  records.
