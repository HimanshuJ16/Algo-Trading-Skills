---
name: multi-currency-pnl-and-fx-conversion
description: Use when a bot or backtest holds positions denominated in more than one
  currency, to stop P&L from different currencies being silently summed as if they
  were the same unit, to resolve point-in-time FX rates without lookahead or silent
  parity fallbacks, and to separate a strategy's price P&L from its incidental
  currency P&L
domain: algorithmic-trading
subdomain: data-management-global
tags:
- data-management-global
- multi-currency
- fx-conversion
- point-in-time-rates
- pnl-decomposition
- iso-4217
- currency-translation
- base-currency
brokers_frameworks:
- ISO 4217
- IFRS IAS 21
- GIPS 2020 (Firms)
- Python standard library (decimal, bisect)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a portfolio spans instruments denominated in more than one currency — a US-listed stock and an India-listed stock in the same conceptual portfolio, a JPY-funded futures position reported in USD, any forex-adjacent multi-asset strategy.

The failure this skill exists to prevent is that **every one of its symptoms looks like a valid number**. `$100 + ₹100 = 200` type-checks, serialises, passes a range assertion, and renders in a dashboard. So does a BTC balance converted to USD at a rate of 1.0, a five-year backtest translated at today's spot, and an exposure figure that a rounding convention quietly zeroed. None of these raise. The discipline is therefore structural: tag every amount with its currency, convert only through a resolver that **raises** when it cannot serve a pair, and never let a fallback substitute a plausible number for a missing one.

## When NOT to Use

- **As an FX pricing or hedging engine.** This translates realised and unrealised amounts between currencies. It does not price forwards, apply interest-rate-parity carry, or size a hedge. Forward points and swap legs belong in `fx-forward-and-swap-position-tracking`; hedge execution in `cross-asset-hedge-execution-synchronization`.
- **As a currency-risk model.** `calculate_decomposed_pnl` attributes FX P&L *after the fact*. It says nothing about the distribution of future currency moves; joint asset-FX covariance lives in `multi-currency-var-aggregation`.
- **As a quote-orientation normaliser.** `PointInTimeFXResolver` trusts the direction its provider reports. Deciding whether a vendor's `USD/EUR` tick is EUR-per-USD or USD-per-EUR is `currency-pair-quoting-convention-normalization`'s job, and it must happen *upstream* of this module — an inverted rate here converts silently and correctly-looking.
- **As a tax or statutory-reporting engine.** The decomposition is a performance-attribution split. Which lots realise a currency gain, and when, is jurisdiction-specific: see `currency-gain-loss-tax-treatment-for-forex-trading` and `multi-jurisdiction-tax-residency-implications`.
- **For a single-currency book.** The tagging discipline in step 1 still applies; the conversion machinery does not.

## Prerequisites

- **A timestamped historical FX rate series** for every pair in use — not a "current spot" endpoint. Backtest-accurate conversion needs the rate that was observable at each event's own timestamp.
- **An explicitly chosen base/reporting currency**, decided deliberately rather than inherited from whichever currency the codebase happened to assume first. GIPS 2020 provision **4.C.9** requires a firm to disclose or otherwise indicate the reporting currency; a system that cannot say which currency a figure is in cannot satisfy that.
- **A settled rate-direction convention.** This module's is fixed and documented: `get_rate(from_ccy, to_ccy)` returns **units of `to_ccy` per one unit of `from_ccy`**. Vendor feeds must be normalised into it before injection.
- **Per-currency precision**, from ISO 4217 for fiat and from venue instrument metadata for crypto — the module ships the ISO exponent table but cannot know a venue's increments.

## Workflow

1. **Tag first, convert later.** Store every position, trade, and P&L figure as a `CurrencyAmount(amount, currency, timestamp)` — never a bare float, *even in a system that currently trades one currency*. The single-currency assumption is exactly what breaks silently the day a second currency is added, because nothing in the type or the value announces the change.

2. **Convert only at the point of aggregation or reporting.** Position-level P&L stays in its native currency so it can be reconciled line-by-line against the broker's own native-currency statement. Converting at trade entry and discarding the native figure destroys that reconciliation permanently — you can no longer tell a broker fee discrepancy from an FX rate discrepancy.

3. **Resolve rates as-of the event's own timestamp.** `HistoricalFXRateStore` returns the newest observation *at or before* the requested time and raises if the request predates the series, rather than borrowing the first known rate. Applying one current rate across a backtest is the multi-currency instance of the concern in `lookahead-bias-elimination`: today's rate is information that did not exist at the trade. Set `require_timestamp=True` on the resolver in any backtest path so an untimestamped lookup — which silently means "latest" — is an error instead of a quiet contamination.

4. **Let a missing rate fail the operation.** When a pair cannot be resolved by any direct, inverse, or pivot path, the resolver raises `FXRateUnavailableError` and `aggregate_in_base_currency` propagates it with the offending leg's index. Do not catch it and skip the leg: a dropped leg understates the aggregate by exactly the amount that mattered, and the result still looks like a number.

5. **Convert before every cross-currency risk aggregation.** A portfolio exposure limit, or the correlation-cluster check in `correlation-aware-exposure-limits`, must aggregate base-currency values. Raw notionals summed across currencies mis-state exposure by whatever the relative unit sizes happen to be — ¥10,000,000 of JPY notional next to $10,000 of USD notional is a 7:1 error in a "20,010,000" total that looks fine.

6. **Round once, at the end, half-up.** `aggregate_in_base_currency` sums at full precision and quantises the total once. Rounding each leg first accumulates error that is invisible in the output and unbounded in the leg count — with a 0-decimal base (JPY, KRW), 1,000 legs of ¥0.5 round to ¥0 each and report an exposure of **zero**. Use ISO 4217 minor units, and half-up rather than Python's `round()`, which is half-to-even on a binary float (`round(2.675, 2) == 2.67`).

7. **Decompose price P&L from FX P&L, and know where the interaction term went.** `calculate_decomposed_pnl` reports the price effect at the entry rate and the FX effect on the exit notional, which sum exactly to the total. That convention places the whole price × FX cross term inside the FX leg, so the module reports `fx_on_entry_notional` and `price_fx_interaction` separately — an attribution system that assigns the cross term elsewhere will disagree with `fx_translation_pnl` while agreeing on `total_base_pnl`. Without this split you cannot tell a strategy with an edge from one that was long a rising currency.

> Full step-by-step procedure and API detail: see `references/workflows.md`.
> Verified standards, citations, and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A conversion fallback that returns 1.0.** Parity is indistinguishable from a correct conversion in the output — no sign, magnitude, or type check catches it. This module's previous default provider did exactly this for any pair outside a nine-currency table, so `BTC → USD` converted at 1.0. A resolver must raise, never guess.
- **Hard-coded "reference" spot rates as a default.** Undated constants ship as a convenience and stay forever. They ignore the timestamp argument entirely, which turns the point-in-time API into a static one without changing a single call site.
- **An inverted rate.** `EUR/USD = 1.09` is USD per EUR, but `USD/JPY = 155` is JPY per USD. A provider that maps pair strings straight onto `(from, to)` is inverted for roughly half the majors, and the result is a plausible number rather than an exception. Normalise orientation upstream and unit-test the provider on a pair quoted each way.
- **Summing raw P&L or notionals across currencies** without conversion, producing an aggregate that looks plausible and means nothing.
- **Converting at trade entry and discarding the native figure**, which makes reconciliation against the broker's native-currency statement impossible after the fact.
- **One current rate applied across historical P&L**, which is lookahead wearing an accounting costume.
- **Rounding each leg before summing**, which drifts without bound in the leg count and can zero a 0-decimal-currency aggregate entirely.
- **A 2-decimal default applied to every currency.** KRW, JPY, CLP, ISK, VND and the CFA francs take 0 decimals; KWD, BHD, OMR, JOD, TND, IQD, LYD take 3. Assuming 2 misstates every rounded figure in those currencies, and payment-processor tables are *not* ISO 4217 — they encode processing conventions and disagree on IDR and CVE among others.
- **Conflating FX translation P&L with price P&L**, hiding whether returns came from the trading edge or from incidental currency exposure — and therefore whether the exposure should be hedged.
- **Assuming an official rate is a realisable rate.** For currencies subject to capital controls (ARS, VES, NGN, EGP at various times), a published official rate may not be the rate at which any conversion can actually occur. IAS 21's *Lack of Exchangeability* amendment (effective for annual periods beginning on or after 1 January 2025) addresses exactly this case; a system that silently applies the official rate will value a position at a price no counterparty offers.
- **Mixing naive and timezone-aware timestamps** in a rate series, which either raises deep inside a comparison or — worse, if normalised carelessly — shifts an as-of lookup across a session boundary onto the wrong day's rate.

## Verification

- Confirm every stored P&L/position record carries an explicit currency field, checked against the **data schema**, not against current behaviour — a single-currency deployment passes a behavioural check trivially.
- Confirm the rate provider refuses an unknown pair. Ask it for a pair it does not carry and assert it raises; a returned `1.0` is a defect, not a default.
- Confirm rate direction on a pair quoted each way: `get_rate("USD", "JPY")` should be ≈150, not ≈0.0067.
- Reconcile a sample of converted base-currency figures against the broker's native-currency statement for the same trades, using the point-in-time rate for each trade's own timestamp.
- Confirm a backtest run against the historical rate series differs from the same backtest run with one current rate, over a period with real FX movement. If the two agree, point-in-time resolution is not actually wired in.
- Confirm an as-of lookup earlier than the first observation raises rather than returning the first known rate.
- Confirm `native_price_pnl + fx_translation_pnl == total_base_pnl` exactly, in a 0-decimal base currency, where independent rounding of the three figures is most likely to break the identity.
- Confirm a cross-currency exposure aggregate is computed from base-currency values on a constructed multi-currency position set, and that a leg whose rate is unavailable fails the aggregate rather than being dropped.
- Run `python -m unittest discover -s skills/multi-currency-pnl-and-fx-conversion/scripts`.

## Related Skills

- `lookahead-bias-elimination`
- `correlation-aware-exposure-limits`
- `currency-pair-quoting-convention-normalization`
- `multi-asset-backtest-currency-normalization`
- `multi-currency-var-aggregation`
- `fx-forward-and-swap-position-tracking`
- `forex-broker-integration-oanda-mt5`
- `currency-gain-loss-tax-treatment-for-forex-trading`
