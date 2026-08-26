# Standards — market-maker-vs-taker-strategy-classification

## What this skill is, and what it is not

This skill measures **realized fills**. It answers "what share of my executed flow added
liquidity, and what did that cost or earn in exchange fees". It does not answer "am I a
market maker", "is my passive strategy working", or "what will the next fee tier cost".

## Liquidity flags are not binary (verified against the FIX specification)

FIX `LastLiquidityInd` (**tag 851**) enumerates the liquidity outcome of a fill:

| Value | Meaning | Treatment here |
|---|---|---|
| `1` | Added Liquidity | Maker side — numerator and denominator of the ratio |
| `2` | Removed Liquidity | Taker side — denominator of the ratio |
| `3` | Liquidity Routed Out | Excluded from the ratio; fees stay in the net |
| `4` | Auction (FIX 5.0 SP2 onward) | Excluded from the ratio; fees stay in the net |

Sources: FIX 4.4 dictionary, [tag 851](https://www.onixs.biz/fix-dictionary/4.4/tagnum_851.html)
(values 1–3); FIX 5.0 SP2 dictionary,
[tag 851](https://www.onixs.biz/fix-dictionary/5.0.sp2/tagnum_851.html) (adds Auction).

**Consequence:** a boolean maker/taker field cannot represent values 3 and 4. Collapsing
them into `is_maker=False` books them as continuous-book taker volume, which they are not,
and bills them at a rate the venue never charged them.

## Not a regulatory market-maker determination

### EU / MiFID II — jurisdiction: EEA trading venues

| Point | Source | Status |
|---|---|---|
| An investment firm engaged in algorithmic trading pursues a "market making strategy" when, dealing on own account, its strategy involves posting firm, simultaneous two-way quotes of comparable size and at competitive prices, providing liquidity on a regular and frequent basis. | Directive 2014/65/EU (MiFID II), **Article 17(4)** | In force |
| The presence obligation is expressed in quoting terms: two-way quotes of comparable size (sizes diverging by less than 50%) at competitive prices, in at least one instrument on a venue, for more than 50% of the daily trading hours of continuous trading excluding auctions, for half of the trading days over a one-month period. | Commission Delegated Regulation (EU) **2017/578 (RTS 8)**, Articles 1–2, as summarised in [ICE Futures Europe and ICE Endex Guidance on Market Making under MiFID II](https://www.ice.com/publicdocs/circulars/17194_attach.pdf) (December 2017) | In force |

**Consequence:** the regulatory test is *quoting presence over time*, measured on quotes.
This skill measures *fills*. A desk can be maker-dominant by fill ratio and nowhere near
the RTS 8 presence threshold, and vice versa. Use `mifid-ii-algo-trading-compliance-eu`
for the obligation itself; the ICE guidance is an exchange restatement, not legal advice,
and firms should read RTS 8 directly.

### US — jurisdiction: United States

The SEC's expanded dealer rules, **Rules 3a5-4 and 3a44-2**, were **vacated** by the US
District Court for the Northern District of Texas on **21 November 2024**, and the SEC
**voluntarily dismissed its appeal on 20 February 2025**. The SEC's Spring 2026 regulatory
agenda signals an intention to propose a narrower rule; nothing is in force in the
meantime. No maker-ratio threshold is, or ever was, a dealer-registration trigger.

## Classification thresholds — a convention, not a standard

| Metric | Engineering standard |
|---|---|
| Pure Maker threshold | Default $R_{\text{maker}} \ge 0.80$. **This is this repository's reporting convention.** No regulator or exchange defines a maker-ratio cut-off; override it to whatever your desk means. |
| Pure Taker threshold | Default $R_{\text{maker}} \le 0.20$, same status. Both bounds are inclusive. |
| Threshold validity | The engine MUST reject a swapped, equal, non-finite, or out-of-`[0,1]` threshold pair — a swapped pair makes the taker branch unreachable and labels every mixed strategy PURE_MAKER. |
| Threshold comparison | MUST compare the full-precision ratio. Rounding to 4 dp before comparing promotes 0.79996 to PURE_MAKER. |
| Boundary proximity | A ratio within 0.005 of a threshold MUST be reported as a cut-off artefact rather than presented as a robust label. |

## Engineering standards

| Metric | Engineering standard |
|---|---|
| Sign convention | `fee_paid_usd` and every reported USD amount are signed: positive = charged by the venue, negative = credited (rebate). A negative `effective_fee_rate_bps` is net rebate capture. Matches `exchange-fee-tier-and-rebate-structure-analysis`. |
| Classification basis | MUST be explicit. Per-unit fee schedules (US equities, quoted in $/share) imply `QUANTITY`; percentage-of-value schedules (crypto) imply `NOTIONAL`. There is no safe default, so the engine requires it. |
| Multi-symbol logs | Share counts are not additive across instruments. The engine MUST refuse a multi-symbol log on the `QUANTITY` basis and MUST report `maker_volume_ratio` as `None` rather than as a number. |
| Undefined ratio | A log with no maker or taker fill MUST report `None` and `UNCLASSIFIED_NO_MAKER_TAKER_VOLUME`, never `0.0` — which reads as "entirely taker". |
| Effective fee rate | MUST be computed in bps against gross notional over **all** fills, including excluded ones: those fees were charged. |
| Fee attribution | Maker-side and taker-side fees MUST be reported separately. A positive maker-side figure means the passive side was charged, not credited. |
| Input validation | `is_maker` MUST be a real `bool` (the string `"false"` is truthy); prices and quantities MUST be finite and strictly positive; fees MUST be finite. NaN propagates silently through every downstream figure otherwise. |
| Numerical handling | Arithmetic is carried in full precision and rounded once at the reporting boundary. |

## Venue pricing models — verify, do not remember

| Model | How the fee is quoted | Implied basis | Examples (verify against the venue's current schedule) |
|---|---|---|---|
| Per share | $/share, maker often negative (rebate) | `QUANTITY` | US equity exchanges |
| Percentage of trade value | % of notional, tiered on 30-day rolling volume | `NOTIONAL` | [Binance](https://www.binance.com/en/fee/schedule), [Kraken](https://www.kraken.com/features/fee-schedule) |
| Per contract by membership | $/contract by membership, product, venue and transaction type — **no liquidity flag** | Out of scope | [CME Group](https://www.cmegroup.com/company/clearing-fees.html) |

On standard published crypto tiers the maker rate is a **positive fee**, not a rebate:
Binance's published spot fee schedule charges a positive maker rate at every VIP tier
including the top one (VIP 9, 0.011% maker / 0.023% taker as published at the time of
writing). Negative maker rates on these venues come from venue-specific market-maker or
liquidity-provider programmes, not from the standard tier table. Do not assume "maker"
implies "paid". Other rates are deliberately not reproduced here — they change without
notice, and a stale rate in a reference file is worse than no rate.

## Known limitations

- **Fills only.** Unfilled passive orders are invisible to a fill log, so the maker ratio
  says nothing about fill rate, queue position, adverse selection, or quoting presence.
- **Single venue.** Per-share and percentage-of-value schedules do not share a pricing
  unit; the engine does not normalise across them.
- **Positive prices only.** Negative settlement prices (WTI, April 2020) would invert the
  sign of every derived figure and are rejected rather than propagated.
- **No fee-schedule modelling.** The engine consumes the fee actually billed per fill; it
  does not reconstruct or verify it from a rate card. Use
  `exchange-fee-tier-and-rebate-structure-analysis` for that.

## Category

`Market Microstructure Latency`
