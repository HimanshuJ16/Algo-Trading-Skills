# Standards & Broker Behavior — multi-asset-backtest-currency-normalization

## Rate quoting convention (verify per vendor before loading anything)

This library stores `rate` = units of `to_currency` per **one** unit of `from_currency`.
Conversion is always `amount_to = amount_from * rate`. The first currency named in a
market pair is the base, the second the quote, so `EUR/USD = 1.10` is
`register_fx_rate("EUR", "USD", d, 1.10)` and `USD/JPY = 150` is
`register_fx_rate("USD", "JPY", d, 150.0)`.

| Source | Published direction | Notes |
|---|---|---|
| [ECB euro foreign exchange reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | Units of foreign currency per **1 EUR** ("all currencies are quoted against the euro (base currency)") | Updated around 16:00 CET on TARGET working days only, from the ~14:10–14:15 CET central-bank concertation procedure. **Not** published on Saturdays, Sundays, 1 January, Good Friday, Easter Monday, 1 May, or 25–26 December. The ECB publishes them for information and discourages using them for transaction purposes. |
| [WMR Closing Spot Rates (16:00 London)](https://www.lseg.com/en/ftse-russell/latest-updates/wmr-closing-spot-rate-benchmarks-designated-critical) | Per-pair market convention | The de-facto benchmark for portfolio valuation, fund NAV calculation and index construction; administered by FTSE Russell (LSEG) and designated a Critical Benchmark under the UK Benchmark Regulation in November 2024. Use it when a backtest must be comparable to a fund's published NAV. |
| Broker/vendor FX feeds | Varies per pair | Verify direction against a known anchor (1 USD is ~150 JPY, not ~0.0067 JPY) before trusting a file. An inverted rate raises nothing and produces a plausible number. |

## Currency codes

[ISO 4217](https://www.iso.org/iso-4217-currency-codes.html) defines three-letter
alphabetic codes (and three-digit numeric codes) for currencies, funds and precious
metals, and specifies the minor unit for each currency. SIX maintains the code list on
behalf of ISO. This library validates codes as `^[A-Z]{3}$` after stripping and
upper-casing, so `"usd "`, `"US"` and `"EURO"` fail loudly rather than opening a second
ledger. It does **not** validate against the live ISO code list, and crypto/stablecoin
tickers are outside ISO 4217 and outside this skill's scope.

## Accounting reference — IAS 21

[IAS 21 *The Effects of Changes in Foreign Exchange Rates*](https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/2021/issued/part-a/ias-21-the-effects-of-changes-in-foreign-exchange-rates.pdf)
is the IFRS standard this module's translation model follows. It is an accounting
standard, **not** a trading regulation: it binds IFRS financial statements, not a
backtest. It is cited here because it defines the conventions a fund's audited NAV will
use, and a backtest that disagrees with them will not reconcile.

| Requirement | Paragraph | Exact wording |
|---|---|---|
| Initial recognition | 21 | "A foreign currency transaction shall be recorded, on initial recognition in the functional currency, by applying to the foreign currency amount the spot exchange rate between the functional currency and the foreign currency at the date of the transaction" |
| Monetary items | 23(a) | "foreign currency monetary items shall be translated using the closing rate" |
| Non-monetary items at historical cost | 23(b) | "translated using the exchange rate at the date of the transaction" |
| Non-monetary items at fair value | 23(c) | "translated using the exchange rates at the date when the fair value was measured" |
| Exchange differences | 28 | "Exchange differences arising on the settlement of monetary items or on translating monetary items at rates different from those at which they were translated on initial recognition during the period or in previous financial statements shall be recognised in profit or loss in the period in which they arise" |
| Definitions | 8 | *Closing rate*: "the spot exchange rate at the end of the reporting period". *Spot exchange rate*: "the exchange rate for immediate delivery" |

Mapping to this implementation: cash balances are monetary items translated at the
valuation-date rate (para 23(a)); positions are marked at their local fair value and
translated at the same date's rate (para 23(c), since the mark and the rate share a
date); `attribute_nav_change()` isolates the para-28 exchange difference from the local
trading result.

## Broker / framework behavior

| Engine | Multi-currency cash representation | FX handling | Evidence |
|---|---|---|---|
| Interactive Brokers (Universal Account) | One balance per currency; a single **base currency** "determines the currency of translation for your statements, the currency used for determination of margin requirement, and for a Cash Account, the currency of products you are allowed to trade" | Statements total in a Base Currency Summary and then detail each additional currency; non-base amounts are converted at the close-of-period rate, and the period-to-period difference is reported separately on a **Cash FX Translation Gain/Loss** line. Buying a product in a currency you do not hold does **not** silently auto-convert the base currency — IBKR extends a margin loan in the traded currency unless you buy the currency first or attach an FX order. | [Base Currency](https://www.ibkrguides.com/orgportal/basecurrency.htm), [Cash Report](https://www.ibkrguides.com/reportingreference/reportguide/cashreport_default.htm) |
| Backtrader | None natively — "the broker is, so to say, a single currency broker" | Community pattern is to subclass `CommissionInfo` so `getvalue(position, price)` returns a value already scaled into the account currency, updating the rate as the backtest advances. Treat as a workaround, not a supported feature. | [Backtrader community — multi-currency portfolio](https://community.backtrader.com/topic/655/multi-currency-portfolio-with-futures-markets) |
| VectorBT | None — cash is a single series per column, or per group when `cash_sharing=True`; the simulation has no currency dimension | Normalize every price series into one reporting currency **before** simulating. There is no FX conversion inside `Portfolio`. | [vectorbt Portfolio API](https://vectorbt.dev/api/portfolio/base/) |

## Known limitations of this implementation

- **Direct pairs only.** No triangulation through a third currency; chaining two quotes
  compounds two spreads and two timestamps, so the cross must be supplied explicitly.
- **Mid-rate valuation, not execution.** Bid/ask spread and broker FX fees are out of
  scope by design and belong to the execution layer.
- **Snapshot ledger.** No transaction journal, so external cash flows (deposits,
  withdrawals, dividends) land in the attribution's local/trading bucket and must be
  netted out before that bucket is read as P&L.
- **Date granularity.** Rates key on `datetime.date`; intraday fixes are out of scope.
- **Floating-point.** No `Decimal`, no per-currency minor-unit rounding.
