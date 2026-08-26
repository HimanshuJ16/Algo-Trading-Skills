# Standards for Multi-Currency P&L and FX Conversion

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| Currency tagging | Every stored amount MUST carry an explicit currency code. An untagged numeric P&L cannot be aggregated safely. | Repository mandate |
| Rate direction | A rate MUST mean *units of `to_ccy` per one unit of `from_ccy`*, fixed module-wide and documented. Vendor quote orientation MUST be normalised upstream. | Module convention; see `currency-pair-quoting-convention-normalization` |
| No parity fallback | An unresolvable pair MUST raise. Returning `1.0` is indistinguishable from a correct conversion in the output. | Repository mandate |
| Point-in-time resolution | Historical conversion MUST use the newest rate observed **at or before** the event timestamp, never a later one. A request preceding the series MUST raise rather than borrow the first known rate. | Repository mandate; `lookahead-bias-elimination` |
| Rate validity | A rate MUST be finite and strictly positive. Zero, negative, and NaN rates usually indicate an inverted or unpopulated quote. | Repository mandate |
| Decimal precision | Fiat rounding MUST follow ISO 4217 minor units. Crypto precision MUST come from venue instrument metadata. | ISO 4217 (SIX Group / SNV) |
| Rounding mode | Monetary rounding MUST be half-up on the decimal value. Python's `round()` is half-to-even on a binary float. | IEEE 754 §4.3; settlement convention |
| Single rounding point | An aggregate MUST be summed at full precision and rounded once. | Repository mandate |
| Reconciliation | Reported decomposition components MUST sum exactly to the reported total. | Repository mandate |
| Reporting currency disclosure | The reporting currency MUST be disclosed or otherwise indicated. | GIPS 2020 for Firms, provision 4.C.9 |

## Verified sources

### ISO 4217 — Codes for the representation of currencies

The standard defines a three-letter alphabetic code, a three-digit numeric code, and a **minor unit exponent** (base 10) relating a currency's major unit to its minor unit. The tables are maintained by **SIX Group on behalf of ISO and the Swiss Association for Standardization (SNV)**. <https://www.iso.org/iso-4217-currency-codes.html>

Every active code whose exponent is **not 2**:

| Exponent | Codes |
|---|---|
| 0 | BIF, CLP, DJF, GNF, ISK, JPY, KMF, KRW, PYG, RWF, UGX, UYI, VND, VUV, XAF, XOF, XPF |
| 3 | BHD, IQD, JOD, KWD, LYD, OMR, TND |
| 4 | CLF, UYW |
| N.A. | XAU, XAG, XPT, XPD (metals); XDR, XTS, XXX (procedural) |

JPY carries exponent 0 because the sen, nominally 1/100 yen, is no longer used in practice. Everything else active is 2, which is why `DEFAULT_MINOR_UNITS = 2` is a defensible fallback for an unrecognised ISO code — but not a silent one: `minor_units_for` logs a warning the first time it sees a code it does not know.

> **Payment-processor tables are not ISO 4217.** Adyen's published currency table lists IDR and CVE as 0-decimal, reflecting its own processing convention; ISO gives both exponent 2. Do not seed a precision table from a PSP's documentation. <https://docs.adyen.com/development-resources/currency-codes>

> **Crypto assets are outside ISO 4217 entirely.** The protocol-native smallest units are the satoshi (10⁻⁸ BTC) and the wei (10⁻¹⁸ ETH); venues then impose their own price and quantity increments, which are neither of those and vary by venue and by instrument. `DEFAULT_CRYPTO_MINOR_UNITS` ships BTC and ETH at 8 as a *conventional display* precision and is explicitly labelled as such — override it from the venue's instrument metadata via `MultiCurrencyPnLEngine(minor_units=...)` or `register_currency_precision`.

### IAS 21 — The Effects of Changes in Foreign Exchange Rates (IFRS)

The standard prescribes how to account for foreign currency transactions and how to translate into a presentation currency. A foreign currency transaction is recorded at the exchange rate on the transaction date; monetary items are retranslated at the closing rate at the end of each reporting period; and exchange differences on monetary items are generally recognised in profit or loss. <https://www.ifrs.org/issued-standards/list-of-standards/ias-21-the-effects-of-changes-in-foreign-exchange-rates/>

**Applicability caveat.** IAS 21 is a *financial reporting* standard for the preparation of financial statements. It is not a performance-attribution standard and does not govern how a trading system splits P&L between price and currency. Its relevance here is conceptual — it is the origin of the transaction-date/closing-rate distinction that motivates entry-rate versus exit-rate valuation — and jurisdictional: an entity reporting under IFRS must be able to reconcile its trading system's currency figures to statements prepared on this basis. US GAAP addresses the same subject under ASC 830, which is **not** identical.

**Lack of Exchangeability (Amendments to IAS 21).** Issued **August 2023**, effective for annual periods beginning on or after **1 January 2025**, with earlier application permitted. IAS 21 previously specified the rate to use when exchangeability was *temporarily* lacking but was silent when the lack was not temporary. The amendments add requirements for determining whether a currency is exchangeable and, when it is not, for estimating the spot rate — the objective being the rate at which an orderly exchange transaction would take place between market participants at the measurement date. They deliberately do not prescribe an estimation technique. <https://www.ifrs.org/news-and-events/news/2023/08/iasb-sets-out-accounting-requirements-for-when-currency-not-exchangeable/>

This is the standards-level statement of a live production hazard: for currencies under capital controls, a published official rate may not be a rate at which any conversion can occur. This module cannot detect that condition — it converts at whatever rate the provider supplies. Detecting non-exchangeability is the provider's responsibility.

### GIPS 2020 — Global Investment Performance Standards for Firms

**Provision 4.C.9:** "The firm must disclose or otherwise indicate the reporting currency." (Section 4, Composite Time-Weighted Return Report; identical requirements appear at 5.C.9, 6.C.9, 7.C.9, 8.C.5, 8.E.5 and 8.G.8 for the other report types.) <https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf>

Where a composite contains portfolios managed in different base currencies, the firm must convert to the composite's currency before calculating a composite return. The GIPS standards do **not** prescribe a conversion method — converting the underlying market values and cash flows, or converting portfolio-level returns, are both acceptable — but the chosen method must be applied consistently. <https://www.gipsstandards.org/qadatabase/5336/>

Applicability: GIPS is a **voluntary** standard for firms claiming compliance in performance presentation. It is not a regulatory requirement, and it does not apply to a proprietary trading book that makes no performance claim to prospective clients.

### Multicurrency performance attribution

The base-currency return on a foreign holding compounds multiplicatively, $(1 + R_{base}) = (1 + R_{local})(1 + R_{fx})$, so the additive expansion

$$R_{base} = R_{local} + R_{fx} + R_{local} \cdot R_{fx}$$

carries a third, **interaction** term. Any two-way additive split of P&L must therefore place that cross term somewhere, and frameworks differ:

- **Ankrim, E. & Hensel, C. (1994), "Multicurrency Performance Attribution," _Financial Analysts Journal_ 50(2)** — incorporates currency forward premiums and a currency-surprise term, separating what interest-rate parity makes knowable in advance from what is genuinely unexpected.
- **Karnosky, D. & Singer, B. (1994), _Global Asset Management and Performance Attribution_, Research Foundation of the CFA Institute.** <https://rpc.cfainstitute.org/sites/default/files/-/media/documents/book/rf-publication/1994/rf-v1994-n3-4444-pdf.pdf> Argues currency must be managed and attributed as an independent decision, with asset allocation and selection computed on the *local return premium* (local return less the applicable interest rate), because interest-rate parity makes part of a foreign holding's return knowable and hedgeable at the time the position is taken. Refinements of the model report the cross product explicitly so that the components fully explain the portfolio return.

`calculate_decomposed_pnl` implements neither framework. It performs a simple two-way split of realised trade P&L — price effect at the entry rate, FX effect on the exit notional — which is exact and is the natural convention for a per-trade ledger, but which folds the entire interaction term into the FX leg and ignores interest-rate differentials altogether. `fx_on_entry_notional` and `price_fx_interaction` are reported separately so the split can be re-cut against a system that assigns the cross term differently.

## Stated limitations

1. **The module has no rates and no opinion about them.** It validates that a rate is finite and positive, and nothing else. A stale, inverted, wrong-venue, or officially-published-but-unrealisable rate passes every check and converts silently. Rate quality is entirely the provider's responsibility.
2. **Rate direction is trusted, not verified.** There is no way for the module to detect that a provider is returning JPY-per-USD when asked for USD-per-JPY. That check belongs upstream, in `currency-pair-quoting-convention-normalization`.
3. **Not an interest-rate-parity attribution.** The decomposition splits realised P&L into price and FX effects. It does not separate the forward premium from the currency surprise, so it will not reproduce a Karnosky-Singer or Ankrim-Hensel attribution and should not be presented as one.
4. **Realised-trade decomposition only.** `calculate_decomposed_pnl` takes a single entry and a single exit. Partial fills at different rates, mid-life mark-to-market, and dividends or coupons received in the native currency each need their own conversion event; the module provides the primitives but not that lifecycle.
5. **Float arithmetic with decimal rounding.** Amounts are `float` throughout and only the rounding step goes through `Decimal`. That fixes the half-even artefact but does not make the arithmetic exact. A ledger that must be exact to the minor unit should carry `Decimal` amounts end-to-end, which is an API change this module does not make.
6. **No cash-ledger or settlement model.** Per-currency cash balances, settlement dates, and funding are out of scope — see `multi-asset-backtest-currency-normalization`.
7. **`HistoricalFXRateStore` is an in-memory reference implementation.** It is not a persistent, versioned, point-in-time rate database. It holds rates in sorted lists per pair, with no restatement history, no vendor provenance, and no concurrency control.
