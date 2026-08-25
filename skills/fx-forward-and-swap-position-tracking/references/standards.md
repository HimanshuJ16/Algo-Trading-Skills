# Standards — fx-forward-and-swap-position-tracking

## Configuration defaults (calibrate before use)

No regulator, exchange, or standards body mandates that a forward be priced by
Covered Interest Rate Parity, nor how often a book must be revalued. Valuation
frequency is a firm policy and a financial-reporting question, not a rule this
library can assert. The values below are this library's defaults; the sourced
market conventions are in the next section.

| Parameter | Default | What it actually does |
|---|---|---|
| `day_count_basis` | verified table below | Per-**currency** money-market denominator. Merged over the table; a single integer is rejected because the two legs of a pair often differ. |
| `default_day_count_basis` | `360` | Used only for currencies absent from the table and the overrides. Every such currency is logged once at WARNING — an unverified convention is a pricing assumption, not a silent default. |
| `pip_factor_overrides` | `{}` | Per-pair points scaling. The built-in rule is 100 where the quote currency is JPY, 10,000 otherwise. Other two-decimal quote currencies must be supplied explicitly. |
| `maturity_buckets` | `0-1M / 1M-3M / 3M-6M / 6M-1Y` (31/92/184/366 calendar days, inclusive upper bounds), then `1Y+` | Gap-report grouping. A reporting convention chosen for this library, not a market standard — align it with your own gap-limit framework. |
| `report_decimals` | `2` | Rounding applied only when building report objects. All intermediate arithmetic runs at full precision. |
| `market_forward_rate` | absent | When supplied per pair, the mark uses the observed outright instead of the CIRP rate. Preferred wherever an outright is quotable. |

## Sourced market conventions

### Money-market day-count basis, by currency

The forward's two legs accrue on their own currencies' conventions. Only
currencies verified against the rate administrator are hard-coded.

| Currency | Basis | Source |
|---|---|---|
| USD | Actual/360 | "In accordance with broader U.S. dollar money market convention, interest is calculated using the actual number of calendar days, but assuming a 360-day year." — [Federal Reserve Bank of New York — SOFR Averages and Index Data](https://www.newyorkfed.org/markets/reference-rates/sofr-averages-and-index) |
| EUR | Actual/360 | Compounded €STR average rates are calculated over the actual number of calendar days on a 360-day year, the euro money-market convention. — [ECB — Compounded €STR average rates and index: calculation and publication rules](https://www.ecb.europa.eu/stats/euro-short-term-rates/interest_rate_benchmarks/WG_euro_risk-free_rates/shared/pdf/ecb.Compounded_euro_short-term_rate_calculation_rules.en.pdf); [ECB glossary — actual/360](https://www.ecb.europa.eu/services/glossary/html/act6a.en.html) |
| GBP | Actual/365 | Sterling RFR Working Group conventions retain Actual/365 for consistency with existing sterling OIS. — [Bank of England — Recommendations for SONIA Loan Market Conventions](https://www.bankofengland.co.uk/-/media/boe/files/markets/benchmarks/rfr/statement-on-behalf-of-rfrwg-recommendations-for-sonia-loan-market-conventions.pdf) |
| JPY | Actual/365 (Fixed) | The Japanese domestic money market and the TONA/TORF successor benchmarks use Act/365 (Fixed) for both legs of a swap. — [Tokyo Tanshi — TORF swap conventions](https://www.tokyotanshi.co.jp/en/market_report/market_data/tona/torf.html); [Bank of Japan Cross-Industry Committee — TONA (Fixing in Arrears) Conventions to Use in Loans](https://www.boj.or.jp/en/paym/market/jpy_cmte/cmt201225c.pdf) |

**Correction encoded in this version**: JPY was previously documented here as
Actual/360. That was the JPY **LIBOR** convention. JPY LIBOR has ceased, and the
domestic yen money market and its successor benchmarks accrue Actual/365
(Fixed). Every yen forward priced on a 360 denominator is mispriced.

**Consequence for pairs with mixed legs.** GBP/USD, USD/JPY, GBP/JPY and every
other cross where the two currencies disagree cannot be priced with one
denominator. Worked example, 6-month GBP/USD, $S = 1.2500$, GBP $4.5\%$, USD
$5.0\%$, $T = 180$ days:

| Basis treatment | Forward | Points |
|---|---|---|
| GBP Act/365, USD Act/360 (correct) | 1.253434 | 34.34 |
| Act/360 forced on both legs | 1.253056 | 30.56 |

A systematic 3.78-pip error, in the same direction, on every sterling trade.

### Currencies not in the table

Conventions for other currencies were **not** verified for this skill and are
not hard-coded. Supply them via `day_count_basis` after confirming each with the
benchmark administrator. The engine logs a WARNING naming any currency it had to
default; do not suppress that log.

### Pip size and forward/swap points

| Fact | Source |
|---|---|
| "A pip refers to the last decimal place quoted for a currency pair. Most currency pairs are priced to 4 decimal places… The exact value of a pip depends on the currency pair and the quoting convention. For example, a pip in USD-JPY denotes the second decimal place, whereas a pip in EUR-CZK denotes the third decimal place." | [Chatham Financial — What is an FX forward curve?](https://cf.com/insights/what-is-an-fx-forward-curve) |
| "Forward rate = current spot rate + forward points deduced from interest rate differential"; the interest-rate differential is the main component of forward pricing, added so that no arbitrage exists. | [Chatham Financial — What is an FX forward curve?](https://cf.com/insights/what-is-an-fx-forward-curve) |

The engine therefore scales points by 100 where the quote currency is JPY and
by 10,000 otherwise. That default is a **two-case approximation of a per-pair
convention**: EUR/CZK is a documented three-decimal counter-example, and other
pairs deviate too. Where the venue's convention is not certain, set
`pip_factor_overrides` explicitly rather than relying on the default.

### Instrument definitions

| Fact | Source |
|---|---|
| An **outright forward** is a contract to exchange two currencies at a rate agreed on the contract date, for delivery **more than two business days** later. A spot transaction settles within two business days. | [BIS — Triennial Central Bank Survey reporting guidelines](https://www.bis.org/statistics/triennialrep/2019survey_guidelinesoutstanding.pdf) |
| An **FX swap** exchanges currencies on a near leg and reverses at a pre-agreed rate on a far leg. Once the near leg has settled, what remains is indistinguishable from an outright forward. Remaining maturity is measured to the settlement date of each leg separately. | [BIS — Triennial Central Bank Survey reporting guidelines](https://www.bis.org/statistics/triennialrep/2019survey_guidelinesoutstanding.pdf); [BIS Quarterly Review — Bank positions in FX swaps: insights from CLS](https://www.bis.org/publ/qtrpdf/r_qt2309b.htm) |

**Consequence encoded in the engine**: a swap is supplied as its two legs with a
shared `contract_id`, opposite sides, and a far leg maturing strictly after the
near leg. Anything else is rejected. A trade inside the two-business-day window
is flagged as a spot exposure rather than an outright forward.

### Covered interest parity does not hold

| Fact | Source |
|---|---|
| Deviations from covered interest parity persist even in calm markets; the residual is the cross-currency basis, explained by FX hedging demand and the cost of committing balance sheet to arbitrage rather than by transaction costs or credit risk. | Borio, Iqbal, McCauley, McGuire and Sushko, [BIS Working Paper 590 — *The failure of covered interest parity: FX hedging demand and costly balance sheets*](https://www.bis.org/publ/work590.htm) (Oct 2016, rev. Nov 2018) |
| Before the 2008 crisis the log forward-spot difference was approximately the interbank rate differential; since then deviations have persisted and varied considerably by currency and over time. | [BIS Working Paper 590](https://www.bis.org/publ/work590.pdf) |

**Consequence encoded in the engine**: `cirp_forward_rate` and
`valuation_forward_rate` are separate fields, and `mtm_basis` records which was
used. A CIRP rate is a benchmark; where an outright is observable, mark to it.

### Mark-to-market is a present value

| Fact | Source |
|---|---|
| The value of a forward before expiration is a discounted quantity, not a raw price difference. | [CFA Institute — Pricing and Valuation of Forward Contracts and for an Underlying with Varying Maturities](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/pricing-valuation-forward-contracts-underlying-varying-maturities) |
| For a currency forward, the cash flow is the notional times the difference between the current and contracted forward rates, discounted to the valuation date — and "the discount rate used must match the currency of the cash flow." | [AnalystPrep (CFA Level II) — The Mark-to-Market Value of a Forward Contract](https://analystprep.com/study-notes/cfa-level-2/the-mark-to-market-value-of-a-forward-contract/) *(secondary source; consistent with the CFA Institute reading above)* |

**Consequence encoded in the engine**: the mark is discounted at $r_q$ on the
quote currency's own day-count basis, and the undiscounted maturity cash flow is
reported alongside it so the carry component is visible rather than implied.

### Accounting touchpoint (informational)

IFRS 9's hedge-accounting chapter permits — but does not require — an entity to
designate only the **spot element** of a forward as the hedging instrument and
account for the **forward element** (the interest-rate differential, i.e. the
forward points) as a cost of hedging in OCI.

**Confidence: unverified against the primary text.** This is stated here from
secondary accounting commentary; the paragraph text on ifrs.org could not be
opened to confirm the wording or paragraph reference during this review. Treat
it as orientation only and confirm the applicable paragraph and any US GAAP
equivalent with the entity's own accounting policy before relying on it.

Either way, this engine does **not** split the spot and forward elements and
performs no effectiveness testing, so its output cannot by itself support a
hedge-accounting designation. Treat `mtm_pv_quote` as an economic mark.

## Known limitations

- **Single tenor point per pair.** `market_rates` carries one spot and one rate
  pair per currency pair. A book holding 1M and 1Y positions in the same pair is
  marked off one point on the curve. There is no interpolation and no term
  structure.
- **Simple interest, not compounding.** The parity relation is implemented in
  the money-market simple-interest form, which is conventional for tenors up to
  a year. Beyond a year, confirm the compounding convention for both currencies
  before relying on the output.
- **No calendar.** `days_to_maturity` is a calendar-day input, not a computed
  business-day count, and the engine applies no holiday or settlement calendar —
  see `global-exchange-holiday-calendar-handling`.
- **No cross-currency basis model.** The engine can mark to an observed forward
  but does not model, interpolate, or forecast the basis.
- **Deliverable forwards only.** NDF cash-settlement mechanics are not modelled;
  the two-sided currency commitment reported here does not describe an NDF.
- **Market risk only.** Settlement risk, counterparty credit exposure, and
  funding are out of scope.
- **Stateless.** No position lifecycle, no realised P&L, no settlement, no
  netting agreements.
- **Conventions change.** Day-count and benchmark conventions moved materially
  through the LIBOR transition — the JPY correction above is the example. Re-verify
  against the administrator rather than trusting a cached table.

## Migration from 1.x (breaking)

| 1.x | 2.0 | Why |
|---|---|---|
| `FxForwardSwapTrackingEngine(day_count_basis=360)` | `FxForwardSwapTrackingEngine()` or `day_count_basis={'AUD': 365}` | A scalar cannot express a pair whose legs differ. An `int` now raises `TypeError`. |
| `report.net_unrealized_mtm_pnl_usd` | `report.unrealized_mtm_pv_by_quote_currency` (+ optional `net_unrealized_mtm_pv_reporting_currency`) | The old field summed different quote currencies and labelled the total USD. |
| `report.net_exposure_by_base_currency` | `report.net_exposure_by_currency` | Both currencies of every pair are now tracked. |
| `detail.fair_market_forward_rate` | `detail.cirp_forward_rate` + `detail.valuation_forward_rate` + `detail.mtm_basis` | The theoretical rate and the rate actually marked against are different things. |
| `detail.unrealized_mtm_pnl_quote` | `detail.undiscounted_mtm_quote` + `detail.mtm_pv_quote` | The old field was undiscounted. Removing the name makes the change fail loudly instead of silently redefining it. |

## Category

`global-market-integration-fx`
