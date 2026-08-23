---
name: currency-pair-quoting-convention-normalization
description: FX market data normalization module that ranks currency pairs against
  the de-facto interbank base/terms convention, cross-inverts backwards quotes
  (USD/EUR -> EUR/USD), sizes pips and spreads, and refuses to transform any pair
  it cannot rank.
domain: Data Management Global
subdomain: FX Market Data Normalization
tags:
- fx-quoting
- currency-pair
- iso-4217
- base-quote-currency
- inverted-quote
- bid-ask-conversion
- pip-calculation
brokers_frameworks:
- ISO 4217
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting FX quotes from multiple vendors (Bloomberg, Refinitiv, Interactive Brokers, retail aggregators) whose symbologies disagree about which currency is the base. A vendor sending `USD/EUR` where your models expect `EUR/USD` produces inverted signals, wrong-side fills, and sign-flipped PnL.

`CurrencyPairQuotingNormalizer` ranks both legs, cross-inverts the quote when the vendor sent it backwards, sizes the pip from the *normalized* terms currency, and reports the result as `STANDARD`, `INVERTED`, or `UNCLASSIFIED`.

**ISO 4217 supplies the three-letter codes, not the ordering.** Its scope is "the structure for a three-letter alphabetic code and an equivalent three-digit numeric code for the representation of currencies" — it defines no base/terms hierarchy. The ranking `EUR > GBP > AUD > NZD > USD > CAD > CHF > JPY` is a de-facto interbank convention, evidenced in `references/standards.md`, and it is configurable for exactly that reason.

## When NOT to Use

- **As a crypto or metals normalizer.** Legs must be three-letter codes, so `BTC/USDT` and `USDT/EUR` are rejected outright. `XAU/USD` parses but is unranked, so it is reported `UNCLASSIFIED` and passed through untouched — which is the point: gold must not be inverted.
- **As a currency-code validator.** The module has no ISO 4217 register. A three-letter code outside the configured ranking is `UNCLASSIFIED` whether it is an exotic currency or a typo; it cannot tell them apart.
- **As a tick-size or rounding authority.** Pip size is a spread-measurement unit here, not a venue tick size. It says nothing about valid price increments for order entry.

## Prerequisites

- A base/terms ranking covering your traded universe. The default covers only the eight majors; pass `priority_list` to extend it (e.g. append `ZAR` so `ZAR/USD` normalizes to `USD/ZAR`).
- Raw quote payload: `raw_symbol`, `bid_price`, `ask_price`, `vendor_id`. Prices must be finite and strictly positive.
- For terms currencies conventionally quoted to two decimals beyond JPY, or for non-FX pairs, the `two_decimal_terms_currencies` and `pip_size_overrides` arguments.

## Workflow

1. **Parse the symbol.** `/`, `_`, `-`, `.`, `:`, whitespace, and the bare six-character form are all accepted. A leg that is not three alphabetic characters is rejected rather than mis-split — `USDT/EUR` must fail loudly, not silently become `USD`/`TEU`. A symbol naming the same currency twice is rejected.
2. **Validate both prices before branching.** Reject non-finite and non-positive prices. A NaN bid propagates silently through subtraction and division, and the resulting NaN spread compares False against every downstream threshold.
3. **Rank both legs.**
   - Both ranked, `index(CUR1) < index(CUR2)` → `STANDARD`, pass through.
   - Both ranked, `index(CUR1) > index(CUR2)` → `INVERTED`, flip and cross-invert.
   - **Either leg unranked → `UNCLASSIFIED`: do not invert.** An unknown currency is not a low-priority currency. Treating it as one flips `XAU/USD` at 2000.10 into `USD/XAU` at 0.0005 and `BTC/USD` into `USD/BTC` at 0.0000167. Leaving an unrankable pair as the vendor sent it is recoverable; inverting it wrongly is not.
4. **Cross-invert, never same-side invert.**
   $$\text{Bid}_{\text{std}} = \frac{1}{\text{Ask}_{\text{inv}}}, \qquad \text{Ask}_{\text{std}} = \frac{1}{\text{Bid}_{\text{inv}}}$$
5. **Size the pip from the normalized terms currency.** `0.01` when the terms currency is conventionally quoted to two decimals (default: `JPY`), `0.0001` otherwise, or an explicit `pip_size_overrides` entry. `JPY/USD` inverts to `USD/JPY`, so its pip becomes `0.01` — reading the pip off the *raw* symbol is a factor-of-100 error. For an `UNCLASSIFIED` pair, `pip_size` and `spread_pips` are `None` rather than a fabricated default; `spread_price` (ask minus bid, in terms-currency units) is always populated.
6. **Check the flags.** `is_crossed` marks a vendor book where bid exceeds ask. Inversion preserves crossing, so the flag always reflects the vendor's data, never an artefact of normalization.

> Full procedure: see `references/workflows.md`.
> Ranking and pip evidence: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Same-side inversion.** Setting $\text{Bid}_{\text{std}} = 1/\text{Bid}_{\text{inv}}$ instead of $1/\text{Ask}_{\text{inv}}$ narrows or negates the spread and can manufacture a crossed market out of a healthy one.
- **Treating "unknown currency" as "lowest priority".** This is the failure that destroys gold and crypto feeds. `XAU` is an ISO 4217 code for one troy ounce of gold and the LBMA Gold Price is set in US dollars per fine troy ounce, so gold is the base; ranking it last inverts the quote into a four-decimal-place fraction and reports ~4000 "pips" of spread.
- **Reading the pip size off the raw symbol.** Pip size follows the *normalized* terms currency. A `JPY/USD` feed normalized to `USD/JPY` needs `0.01`, not the `0.0001` its raw terms currency would suggest.
- **Rounding normalized prices before publishing them.** Rounding `1.0995052` to `1.09951` and reporting a spread computed from the unrounded value leaves the report internally inconsistent: a consumer recomputing `(ask - bid) / pip` from the published prices gets 4.9 where the report says 4.96.
- **Trusting `is_inverted == False` as proof of correctness.** Under the old two-state model it also covered pairs the module could not rank. Check `classification == "STANDARD"` when you need a positive assurance.
- **Silently accepting a crossed book.** A negative `spread_pips` reaching a cost model understates transaction costs; gate on `is_crossed`.

## Verification

- Inverted `USD/EUR` at bid $0.9090$ / ask $0.9095$ normalizes to `EUR/USD` with bid $1/0.9095 = 1.099505$, ask $1/0.9090 = 1.100110$, and $6.05$ pips.
- Standard `USD/JPY` at $150.00/150.03$ keeps its symbol, uses pip $0.01$, and reports $3.0$ pips. Feeding the same market as `JPY/USD` ($1/150.03$ / $1/150.00$) must produce the identical normalized quote and pip size.
- `XAU/USD` at $2000.10/2000.50$ must come back `UNCLASSIFIED`, unchanged, with `pip_size is None` and `spread_price` $\approx 0.40$ — never `USD/XAU` at $0.0005$.
- A NaN or negative bid must raise, not yield a NaN spread.
- Recomputing `(normalized_ask - normalized_bid) / pip_size` from the report must reproduce `spread_pips`.
- Run `python -m unittest discover -s skills/currency-pair-quoting-convention-normalization/scripts`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `multi-source-price-reconciliation-tie-breaking`
- `multi-currency-pnl-and-fx-conversion`
- `vendor-specific-adjustment-methodology-reconciliation`
- `cross-vendor-timestamp-precision-reconciliation`
