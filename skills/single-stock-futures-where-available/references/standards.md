# Standards & Sources for Single Stock Futures (Where Available)

## Nobody publishes an arbitrage threshold or a "fair value" you must use

**No exchange, clearing house or regulator publishes a mispricing threshold that
constitutes an arbitrage, nor a theoretical value a trader is required to compute.**
An earlier revision of this file presented "±0.3% mispricing" in a table of "Standard
Rules", which was wrong: it dressed a placeholder as a published requirement.

`arbitrage_cost_threshold_pct = 0.3` exists so the module runs out of the box. It must
be replaced by your own measured round-trip cost, which has to cover:

| Cost component | Note |
|---|---|
| Brokerage and exchange transaction charges | Both legs, both directions |
| Clearing and settlement fees | Including physical settlement charges at expiry where applicable |
| Bid-offer | Crossed on both the spot and futures leg |
| Market impact | Sized to the lot, not to the top of book |
| Funding spread | Your actual funding rate over the `risk_free_rate_annual` you priced with |
| Borrow fee | On the reverse leg, at the rate actually quotable for the holding period |
| Transaction taxes | India: STT and stamp duty on both legs and on physical settlement |
| Margin carry | Both legs financed to expiry, including variation margin on the futures leg |

## The pricing model and why it is a band

The base relationship is the standard cost-of-carry forward for an asset paying known
discrete cash flows, $F = (S - \text{PV}(D))e^{rT}$ — see John C. Hull, *Options,
Futures, and Other Derivatives*, chapter on "Determination of Forward and Futures
Prices". That form assumes the underlying can be borrowed and lent freely at $r$.

Once borrowing the stock costs money, the two arbitrages carry differently and the
no-arbitrage region becomes an interval:

| Edge | Expression | Who defends it | Why |
|---|---|---|---|
| Ceiling | $(S - \text{PV}(D))e^{(r - s_{\text{lend}})T}$ | Cash-and-carry: buy spot, sell future | The long funds the stock at $r$ and earns only lending income it has actually contracted. Default $s_{\text{lend}} = 0$. |
| Floor | $(S - \text{PV}(D))e^{(r - s_{\text{borrow}})T}$ | Reverse cash-and-carry: short spot, buy future | The short must borrow the shares and pay the fee for the whole holding period. |
| Reference | $(S - \text{PV}(D))e^{rT}$ | Nobody | Reported as `theoretical_fair_value` for continuity. It is the zero-borrow-cost case and is **not** the signal trigger. |

**This band construction is this skill's engineering choice**, not a published exchange
formula. What it prevents is well documented empirically, though: a stock's futures and
forward prices embed its lending fee, so hard-to-borrow names trade at a discount to the
naive forward that is *rational* rather than arbitrageable. Screening such a name
against a single fair value computed at $e^{(r - s_{\text{borrow}})T}$ — as this
module's v1.0.0 did in both directions — inverts the sign of the error exactly where the
borrow fee is largest.

**Day count.** ACT/365 fixed by default, ACT/360 selectable. Neither is mandated: venues
publish contract terms, not a day-count basis for a trader's own theoretical value.

## Settlement: a per-contract term, not a venue-wide label

| Venue | Documented behaviour | Source |
|---|---|---|
| **NSE India** | All stock derivatives are compulsorily **physically settled**. SEBI set out the framework in a February 2019 circular (SEBI/HO/MRD/DOP1/CIR/P/2019/28) following its April 2018 decision, phased by liquidity: bottom 50 stocks from the April 2019 expiry, the next 50 from July 2019, and **all remaining stocks from the October 2019 expiry**. | [SEBI circular, 8 Feb 2019](https://www.sebi.gov.in/legal/circulars/feb-2019/physical-settlement-of-stock-derivatives_42021.html) |
| **Eurex** | Single Stock Futures are available in **both** cash-settled and physically deliverable variants across its European underlyings; settlement is a term of the individual contract. Physically fulfilled contracts deliver on the second exchange day after the last trading day, directly between Clearing Members. | [Eurex Single Stock Futures product page](https://www.eurex.com/ex-en/markets/equ/fut); Eurex Contract Specifications, Part 1 |
| **CME (from 2026)** | Single Stock Futures are **cash-settled** futures on the price of an individual company. | [CME Group press release, 30 June 2026](https://www.cmegroup.com/media-room/press-releases/2026/6/30/cme_group_to_launchsinglestockfuturesonjuly27.html) |

**Consequence for a cash-and-carry.** On a physically settled contract an unclosed leg
at expiry is a delivery obligation for the full notional — purchase consideration on the
long side, deliverable shares on the short — not a cash difference. This is why the
engine surfaces `physical_delivery_at_expiry` rather than storing `settlement_type` and
never reading it.

## Availability: "where available" is load-bearing

| Period | US availability | Source |
|---|---|---|
| Until 18 Sep 2020 | OneChicago, the last US venue listing single stock futures, ceased trading. Its withdrawal from registration as a national securities exchange for security futures was granted in February 2021. | [SEC order, Federal Register, 18 Feb 2021](https://www.federalregister.gov/documents/2021/02/18/2021-03218/self-regulatory-organizations-onechicago-llc-order-granting-onechicago-llcs-request-to-withdraw-from) |
| 18 Sep 2020 – 27 Jul 2026 | No US venue listed single stock futures. | As above |
| From 27 Jul 2026 | CME Group launched Single Stock Futures: 55 standard and 22 Micro contracts on 50+ US equities, cash-settled, covering roughly 55–65% of the S&P 500 and Nasdaq-100 by weighting. Distributed through retail brokers including Charles Schwab / thinkorswim. | [CME Group press release, 30 June 2026](https://www.cmegroup.com/media-room/press-releases/2026/6/30/cme_group_to_launchsinglestockfuturesonjuly27.html); [Katten, "The Return of Security Futures"](https://katten.com/the-return-of-security-futures-what-cmes-single-stock-futures-mean-for-broker-dealers-and-fcms) |

A universe or backtest spanning that gap will price contracts that did not exist.

## Margin: jurisdiction-specific, and mostly not a flat percentage

| Jurisdiction / venue | Documented requirement | Source |
|---|---|---|
| **US — security futures** | Minimum customer margin is **15% of the current market value** of each unhedged long or short security future, lowered from 20% by joint CFTC/SEC amendments to CFTC Rule 41.45 and SEC Rule 403, **effective 24 December 2020**. Strategy-based offsets permit lower margins with a floor of 5%. These are minimums; the FCM or broker-dealer may require more. | [SEC final rule 34-90244](https://www.sec.gov/files/rules/final/2020/34-90244.pdf); [Federal Register, 24 Nov 2020](https://www.federalregister.gov/documents/2020/11/24/2020-24353/customer-margin-rules-relating-to-security-futures) |
| **US — spot equity on margin** | Initial margin for a margin equity security is **50%** of its current market value, or the amount required by the regulatory authority where the trade occurs, whichever is greater. | [Regulation T, 12 CFR 220.12](https://www.ecfr.gov/current/title-12/chapter-II/subchapter-A/part-220/section-220.12) |
| **India — NSE stock futures** | Initial margin is computed by **SPAN** at 99% value at risk over a one-day horizon (two days where mark-to-market cannot be collected before the next trading day), subject to a minimum of 5% of contract value, **plus an Extreme Loss Margin of 3.5%** for stock futures. Scenario-based on the portfolio; **not** a flat percentage of notional. | [NSE Clearing — Margins, equity derivatives](https://www.nseclearing.in/risk-management/equity-derivatives/margins) |
| **Eurex** | Eurex Clearing margins with **Prisma**, a portfolio-based margining methodology, so the requirement depends on the whole portfolio rather than on any per-contract percentage. | [Eurex Single Stock Futures product page](https://www.eurex.com/ex-en/markets/equ/fut) |

**Consequence.** The engine supplies default margin percentages **only** for venues in
`FLAT_MARGIN_VENUES` (currently CME, using the US statutory minimums). For NSE, Eurex
and Euronext it raises `SSFConfigError` and asks for your clearing member's figures,
because a leverage multiplier derived from an invented percentage would be quoted
downstream as if it had been measured. v1.0.0 defaulted every venue to 15%/50% and
reported "3.33x leverage" for NSE contracts.

`leverage_multiplier` is `spot_margin_pct / ssf_margin_pct` and means nothing beyond
that ratio. It is not a measure of capital efficiency: the futures leg is marked to
market daily and can call variation margin the spot leg would not.

## Ex-dividend contract adjustment (India)

| Area | Documented requirement | Source |
|---|---|---|
| Threshold | Adjustment of derivative contracts is carried out where the declared dividend is **at or above 2% of the market value of the underlying stock**. Dividends below 2% are deemed **ordinary** and **no adjustment is made**. The threshold was revised from 5% by this circular. | SEBI circular **SEBI/HO/MRD2/MRD2_DCAP/P/CIR/2022/90**, 28 June 2022 ([SEBI](https://www.sebi.gov.in/legal/circulars/jun-2022/adjustment-in-derivative-contracts-for-dividend-announcements_60306.html)) |
| Reference price for the test | The market price for the test is the closing price of the scrip on the day previous to the date on which the dividend announcement is made by the company after its board meeting. | [NSE — Adjustments in case of Corporate Actions](https://www.nseindia.com/static/products-services/equity-derivatives-corporate-actions-adjustments) |
| Futures adjustment | For an extraordinary dividend, the **base price of the futures contract** is the reference rate less the aggregate dividend, where the reference rate is the **daily mark-to-market settlement price of the relevant futures contract** — not the spot price. | NSE Clearing, Corporate Actions Adjustment |
| Option adjustment | The total dividend amount (special and/or ordinary) is deducted from all strike prices of the option contracts on that stock. | SEBI circular as above |
| Timing | Adjustments are carried out on the last day the security trades cum-basis in the underlying market, after the close of trading hours. | [NSE — Adjustments in case of Corporate Actions](https://www.nseindia.com/static/products-services/equity-derivatives-corporate-actions-adjustments) |

**Jurisdiction.** This is the Indian rule. Eurex and CME publish their own
corporate-action methodologies, which this module does not reproduce; the
`extraordinary_threshold_pct` parameter exists so another venue's threshold can be
passed rather than the Indian one being universalised.

**Scope.** Only cash dividends are modelled. Bonus issues, splits, rights, mergers,
demergers and spin-offs are handled by an exchange-published adjustment factor applied
to the strike and the market lot, which is out of scope here.

## Short selling constraints on the reverse leg (India)

| Area | Documented requirement | Source |
|---|---|---|
| Naked short selling | **Not permitted.** All investors must mandatorily honour their obligation of delivering the securities at the time of settlement. | [SEBI, Broad framework for short selling](https://www.sebi.gov.in/sebi_data/commondocs/ssframe_p.pdf) |
| Who may short | All classes of investors, retail and institutional, may short sell. **No institutional investor may square off intra-day.** Institutional investors must disclose a short sale upfront at order placement; retail investors by the end of trading hours. | [SEBI, Broad framework for short selling](https://www.sebi.gov.in/sebi_data/commondocs/ssframe_p.pdf) |
| Borrow source | Short selling depends on the Securities Lending and Borrowing (SLB) scheme, open to all categories of investors. | [NSE Clearing — SLBS](https://www.nseclearing.in/clearing-settlement/slbs) |

**Consequence.** A `REVERSE_CASH_AND_CARRY` signal is a *pricing* verdict. Its short
spot leg is only executable if the name is borrowable in SLB at the rate you priced, for
the full holding period. The engine adds an explicit warning to `audit_notes` on every
reverse signal for exactly this reason.

## This skill's engineering rules

Everything below is a choice made by this skill. **None of it is published by a
regulator, an exchange, or a clearing house.**

| Rule | Requirement | Why |
|---|---|---|
| Band, not point | The screen MUST compare against two edges separated by the borrow fee. | A single fair value at $e^{(r-s_{\text{borrow}})T}$ flags a rational hard-to-borrow discount as rich, which is where the error is largest. |
| Default lending income | `lending_income_rate_annual` MUST default to 0.0. | An uncontracted lending fee is not income, and assuming it lowers the ceiling and manufactures cash-and-carry signals. |
| Band ordering | `lending_income_rate_annual > short_borrow_rate_annual` MUST be rejected. | It inverts the band, making every price simultaneously too rich and too cheap. |
| Comparison precision | Threshold comparisons MUST use unrounded values. | Rounding first turns a 0.2996% edge into a 0.30% trigger that does not cover its own costs. |
| Non-finite inputs | NaN/Inf MUST be rejected, not floored. | `max(0.01, nan)` returns `0.01` and `nan >= threshold` is `False`, so a corrupted quote reads as a confident signal. |
| Rate plausibility | A rate outside $(-1, 5)$ MUST be rejected. | Catches the percent-versus-decimal error, which inflates the forward by $e^{6T}$. |
| Dividend window | Dividends outside $[0, T]$ MUST be excluded, logged and counted. | A schedule silently dropped for a unit error produces a fair value that is too high with nothing in the output saying so. |
| Dividend magnitude | $\text{PV}(D) \ge S$ MUST raise. | A dividend stream worth more than the share is a data error, not a negative forward. |
| Margin honesty | A flat margin percentage MUST NOT be defaulted for a scenario-margined venue. | A leverage figure from an invented percentage is quoted downstream as if measured. |
| Adjustment gate | The ex-dividend adjustment MUST be gated on the ordinary/extraordinary test. | Adjusting for an ordinary dividend restates a base price the exchange never moved. |
| Model provenance | Every result MUST record which pricing model produced it. | So a future model cannot silently replace this one in place. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `arbitrage_cost_threshold_pct` | `0.3` (%) | Placeholder. Not published by anyone. Replace with your measured round-trip cost. |
| `risk_free_rate_annual` | `0.05` | Placeholder. Use your actual funding curve for the currency and tenor. |
| `short_borrow_rate_annual` | `0.005` | Placeholder. Use the rate quotable for the name and holding period; on a squeezed name it can be orders of magnitude higher. |
| `lending_income_rate_annual` | `0.0` | Deliberately conservative. Raise it only for lending you have contracted. |
| `day_count_basis` | `365.0` (ACT/365 fixed) | Convention choice. ACT/360 available. |
| `ssf_margin_pct` / `spot_margin_pct` | `None` | Defaulted only for CME, from US statutory minimums. Required input elsewhere. |
| `extraordinary_threshold_pct` | `2.0` (%) | The Indian rule. Pass another venue's threshold rather than universalising this one. |

## Scope boundary

This module screens a price snapshot. It places no orders, tracks no position, models no
fill, locates no borrow, and computes no margin call. Its output is a pricing verdict
under operator-supplied costs and rates, never a statement that a trade is executable or
profitable. It is not a compliance artifact and asserts no regulatory obligation; the
regulatory material above is cited to explain venue behaviour the pricing depends on.
