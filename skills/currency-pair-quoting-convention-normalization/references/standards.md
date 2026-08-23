# Standards for Currency Pair Quoting Convention Normalization

## What ISO 4217 does and does not cover

| Claim | Verification |
|---|---|
| ISO 4217 supplies the three-letter codes used throughout this skill. Its scope is limited to "the structure for a three-letter alphabetic code and an equivalent three-digit numeric code for the representation of currencies." | [ISO 4217:2015](https://www.iso.org/standard/64758.html) — scope statement |
| **ISO 4217 defines no base/terms ordering.** Nothing in its scope addresses base currency, quote currency, pair ordering, or FX quotation conventions. Any claim that a pair ordering is "the ISO 4217 hierarchy" is incorrect. | Same source — the scope text makes no reference to quotation conventions |
| ISO 4217 also covers precious metals as "currency units" of one troy ounce, using an `X` prefix plus the chemical symbol: `XAU` (gold), `XAG` (silver), `XPT` (platinum), `XPD` (palladium). The register is maintained by SIX Group on behalf of ISO/SNV. | ISO 4217 code register |

The base/terms ranking below is therefore recorded as a **de-facto market
convention**, not a standard, and it is a constructor argument rather than a
hard-coded rule.

## Evidence for the base/terms ranking

| Ranking claim | Evidence |
|---|---|
| EUR ranks above USD, JPY, GBP and CHF (EUR is the base) | The ECB publishes its euro foreign exchange reference rates for 29 currencies as "EUR 1 = x foreign currency units" — the euro is the base in every one. [ECB euro foreign exchange reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) |
| USD ranks above CHF and above unlisted minors (USD is the base) | The BIS Triennial Central Bank Survey names the pairs `USD/CNY`, `USD/CHF` and `USD/HKD` in exactly that order. [BIS, OTC foreign exchange turnover in April 2025](https://www.bis.org/statistics/rpfx25_fx.htm) |
| Gold is the base against USD; `USD/XAU` is not a market convention | The LBMA Gold Price is set twice daily in **US dollars per fine troy ounce**, administered by ICE Benchmark Administration. [LBMA — About LBMA Daily Auction Prices](https://www.lbma.org.uk/prices-and-data/about-lbma-daily-auction-prices) |

The relative ranking of GBP, AUD and NZD against USD, and of CAD and JPY below
USD, is the conventional interbank ordering carried over from the previous
version of this skill. It matches the pair names in common use but is **not
sourced to a normative publication**, because none exists. Treat the default
ranking as a starting configuration to confirm against your own vendors'
symbologies, not as an authority.

## Engineering rules enforced by the module

| Rule | Rationale |
|---|---|
| Cross-inversion: $\text{Bid}_{\text{std}} = 1/\text{Ask}_{\text{inv}}$, $\text{Ask}_{\text{std}} = 1/\text{Bid}_{\text{inv}}$ | Same-side inversion narrows or negates the spread and can manufacture a crossed market. |
| An unrankable currency is **never** inverted | "Unknown" is not "lowest". Ranking `XAU` last flips a $2000.10 gold quote to $0.0005. |
| Pip size follows the **normalized** terms currency | `0.01` for terms currencies conventionally quoted to two decimal places (default `JPY`), `0.0001` otherwise. Reading it off the raw symbol is a factor-of-100 error on any inverted JPY feed. |
| `pip_size` and `spread_pips` are `None` for an `UNCLASSIFIED` pair | The pip convention follows from knowing the terms currency's quoting convention; fabricating `0.0001` for gold or crypto reports a false measurement. `spread_price` needs no convention and is always populated. |
| Prices are reported unrounded | Rounding before publication is lossy and desynchronizes `spread_pips` from `(ask - bid) / pip_size` as reported. |
| Non-finite and non-positive prices are rejected on every path | An FX rate is a ratio of positive amounts; a NaN propagates silently and compares False against every downstream threshold. |

## Scope notes

- Sources above were verified in August 2026.
- The module holds no ISO 4217 register, so it cannot distinguish an exotic
  currency code from a typo; both are reported `UNCLASSIFIED`.
- Pip size here is a spread-measurement unit, not a venue tick size, and implies
  nothing about valid price increments for order entry.
