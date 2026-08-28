---
name: single-stock-futures-where-available
description: >-
  Pricing a listed single stock future as a no-arbitrage band rather than a single fair value: a cash-and-carry ceiling and a reverse cash-and-carry floor separated by the stock's borrow fee, discrete dividend present values, the SEBI/NSE 2% gate that decides whether an ex-dividend contract adjustment happens at all, and a margin comparison that refuses to invent a percentage for venues that margin scenario-wise (NSE SPAN+ELM, Eurex Prisma). Covers NSE India, Eurex, Euronext and the CME contracts relisted in July 2026.
domain: Derivatives & Arbitrage Trading
subdomain: Single Stock Futures & Cash-and-Carry
tags: ["single-stock-futures", "ssf", "cash-and-carry", "no-arbitrage-band", "borrow-cost", "eurex", "nse-india", "cme", "dividend-adjustment", "security-futures-margin"]
brokers_frameworks: ["NSE / NSE Clearing (SEBI)", "Eurex / Eurex Clearing Prisma", "CME Group Single Stock Futures", "CFTC Rule 41.45 / SEC Rule 403", "Regulation T (12 CFR 220.12)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you hold a spot price, a listed single stock future on the same underlying, a dividend schedule and a borrow cost, and you need a defensible answer to *is this future priced outside the range cash-and-carry arbitrage can defend?*

The central idea is that **there is no single fair value once the stock has a borrow cost**. The textbook forward $F = (S - \text{PV}(D))e^{rT}$ assumes shares can be borrowed and lent freely at the risk-free rate; they cannot. The two arbitrages have different carry, so the no-arbitrage region is a **band**:

- **Cash-and-carry ceiling** — buy spot, sell the future. You fund the stock at $r$ and earn whatever the shares can *contractually* be lent out for. Ceiling: $(S - \text{PV}(D)) \cdot e^{(r - s_{\text{lend}})T}$, with $s_{\text{lend}}$ defaulting to **0**, because a lending fee you have not contracted is not income.
- **Reverse cash-and-carry floor** — short spot, buy the future. You must borrow the shares and *pay* the fee. Floor: $(S - \text{PV}(D)) \cdot e^{(r - s_{\text{borrow}})T}$.

A price inside that band is not mispriced, however far it sits from the naive forward. This matters most on hard-to-borrow names, whose futures trade at a deep discount *precisely because* the borrow is expensive — the exact population a single-fair-value screen flags hardest and is most wrong about.

The engine also gates the ex-dividend contract adjustment on the SEBI/NSE ordinary-vs-extraordinary test, reports whether an open leg at expiry becomes a physical delivery obligation, and compares SSF against spot margin only where a flat percentage is actually the venue's requirement.

## When NOT to Use

- **As an execution or order-routing engine.** This module produces a screening verdict from a snapshot. It places nothing, tracks no position, and models no fill.
- **With `arbitrage_cost_threshold_pct` left at its 0.3% default.** No exchange or regulator publishes an arbitrage threshold. 0.3% is a placeholder so the module runs. Your threshold must cover commissions, exchange and clearing fees, bid-offer on both legs, market impact, the funding spread over the risk-free rate you used, transaction taxes (STT and stamp duty in India), and the cost of carrying margin on both legs to expiry. Set it from your own measured round-trip cost or the screen is decorative.
- **On a name whose borrow you have not located.** Every `REVERSE_CASH_AND_CARRY` signal presumes the short spot leg is executable. In India naked short selling is prohibited and every short must be honoured at settlement, so the leg depends on an SLB borrow that may not exist at the rate you priced. A signal is not a locate.
- **As a source of margin numbers for NSE or Eurex.** NSE Clearing margins stock futures with SPAN (99% VaR) plus a 3.5% Extreme Loss Margin; Eurex Clearing uses Prisma portfolio margining. Neither is a flat percentage of notional, so the engine **raises `SSFConfigError`** for those venues rather than inventing one. Pass your clearing member's actual figures.
- **For the corporate actions this module does not model.** Only cash dividends are handled. Bonus issues, splits, rights, mergers, demergers and spin-offs adjust the contract by an exchange-published adjustment factor applied to the lot size and strike, which is not implemented here — see `corporate-action-event-calendar-integration`.
- **Where the product is not listed.** "Where available" is load-bearing. US single stock futures were unavailable between OneChicago's closure on 18 September 2020 and CME's relaunch on 27 July 2026. Confirm the contract exists on the venue before pricing it.

## Prerequisites

- `SSFContractSpec`: `symbol`, `underlying_spot_symbol`, `exchange`, `lot_size`, `days_to_expiry`, `settlement_type`, `risk_free_rate_annual`, `short_borrow_rate_annual`, `lending_income_rate_annual`, `currency`, `day_count_basis`.
- A spot price and a market SSF price in the same currency as the contract.
- Optional `DividendEvent` schedule (`ex_date_days`, `amount_per_share`) — cash dividends only.
- **A measured `arbitrage_cost_threshold_pct`**, not the shipped default.
- For NSE / Eurex / Euronext: `ssf_margin_pct` and `spot_margin_pct` from your clearing member. For CME the engine falls back to the US statutory minimums (15% security futures, 50% Reg T) and says so in `margin_basis`.

## Workflow

1. **Validate before computing anything.** `compute_fair_value_and_arbitrage` rejects rather than repairs, because each of these otherwise yields a confident wrong signal instead of an error:
   - **NaN / Inf prices** — `max(0.01, nan)` returns `0.01` and `nan >= threshold` is `False`. In v1.0.0 a NaN spot produced a fair value of 0.01 and a confident `CASH_AND_CARRY` against a 2,530 market price.
   - **Non-positive prices, non-positive lot size, negative `days_to_expiry`.**
   - **Rates outside $(-1, 5)$** — catches the percent-versus-decimal error, where passing `6` for 6% inflates the forward by $e^{6T}$.
   - **`lending_income_rate_annual > short_borrow_rate_annual`** — the lender cannot earn more than the borrower pays, and the inequality would invert the band so that every price is simultaneously too rich and too cheap.
2. **Present-value the dividends inside the window.** Cash dividends with `0 <= ex_date_days <= days_to_expiry` discount at $D_i e^{-r t_i}$. Anything outside the window is excluded, **logged, and counted in `excluded_dividends`** — a schedule silently ignored because its ex-dates arrived in the wrong unit produces a fair value that is too high with nothing in the output to say so. If $\text{PV}(D) \ge S$ the call raises: a dividend stream worth more than the share is a data error, not a forward with a negative price.
3. **Build the band, not a point.** Ceiling at $(S - \text{PV}(D))e^{(r - s_{\text{lend}})T}$, floor at $(S - \text{PV}(D))e^{(r - s_{\text{borrow}})T}$. `theoretical_fair_value` is reported as the zero-borrow-cost reference $(S - \text{PV}(D))e^{rT}$ for continuity — it is **not** the trigger, and when the borrow fee is large it sits far above the floor by design.
4. **Screen against the widened band edges, on unrounded values.**
   - $F_{\text{market}} \ge \text{ceiling} \times (1 + c) \implies$ `CASH_AND_CARRY` (buy spot, sell SSF).
   - $F_{\text{market}} \le \text{floor} \times (1 - c) \implies$ `REVERSE_CASH_AND_CARRY` (short spot, buy SSF).
   - Otherwise `NEUTRAL`.
   Comparisons run on raw values and rounding is applied to report fields only, so a gross edge of 0.2996% against a 0.30% threshold is `NEUTRAL` and not a 0.30% trigger. `gross_edge_pct` — the signed excess beyond the *violated* edge, zero when neutral — is the tradeable number; `mispricing_pct` against the carry-neutral reference is reported for continuity and is **not** what fires the signal.
5. **Read the settlement flag before planning the unwind.** `physical_delivery_at_expiry` is `True` for `PHYSICAL_DELIVERY` contracts, which since the October 2019 expiry is every NSE stock future. An unclosed leg is then a delivery obligation for the full notional — full purchase consideration on the long side, deliverable shares on the short — not a cash difference. CME's 2026 contracts are cash-settled; Eurex lists both variants and the settlement is a per-contract term, not a venue-wide one.
6. **Gate the ex-dividend adjustment; do not apply it.** `calculate_ex_dividend_price_adjustment` measures the dividend against the underlying's market price and only adjusts at or above the threshold (2% under SEBI). Below it the dividend is ordinary, the exchange moves nothing, and the returned `adjusted_base_price` equals the input. The adjustment, when it happens, deducts the dividend from the **contract's own previous mark-to-market settlement price**, not from the spot.
7. **Take the margin comparison only where a flat percentage is real.** `leverage_multiplier` is `spot_margin_pct / ssf_margin_pct` and means nothing more than that ratio. It is not a measure of capital efficiency: a futures leg is marked to market daily and can call variation margin the spot leg would not.

> Full procedure: see `references/workflows.md`.
> Sources, jurisdictions, and what no venue publishes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying the short-seller's borrow fee to the long side's ceiling.** v1.0.0 computed one fair value at $e^{(r - s_{\text{borrow}})T}$ and screened both directions against it. That pushes the ceiling down by the borrow fee, so a hard-to-borrow name whose future trades at a rational discount reads as *rich*. At a 20% borrow fee on a 2,500 stock 30 days out, v1.0.0 valued the future at 2,471.40 and called a 2,495.00 market price a `CASH_AND_CARRY` at +0.95%; the band $[2471.40, 2512.36]$ contains it and the correct verdict is `NEUTRAL`.
- **Subtracting PV(dividends) *and* applying a dividend yield $q$.** $(S - \text{PV}(D))e^{(r-q)T}$ double-counts the dividends. The discrete and continuous treatments are alternatives, not layers. This module uses the discrete one, and the exponent carries only funding and borrow terms.
- **Adjusting a futures base price for an ordinary dividend.** Under SEBI circular SEBI/HO/MRD2/MRD2_DCAP/P/CIR/2022/90 (28 June 2022) a dividend below 2% of the underlying's market value is *ordinary* and **no contract adjustment is made** — the drop is absorbed by the market price. Deducting it anyway restates a base price the exchange never moved, and every downstream P&L and margin figure inherits the error. The threshold was 5% before that circular, so a backtest spanning June 2022 needs both.
- **Assuming NSE single stock futures are cash-settled.** They have been compulsorily **physically settled** since the October 2019 expiry, under the SEBI framework phased in from April 2019. An algorithm that expects a cash difference will find itself owing shares or full purchase consideration.
- **Applying US margin numbers to a non-US venue.** 15% (CFTC Rule 41.45 / SEC Rule 403, effective 24 December 2020, lowered from 20%) and 50% (Reg T, 12 CFR 220.12) are US statutory minimums. NSE uses SPAN plus a 3.5% ELM on stock futures; Eurex uses Prisma. Quoting a 3.33x leverage figure for an NSE contract asserts a margin requirement NSE does not set.
- **Reading "no borrow needed" as "no borrow cost".** A short future needs no locate, which is a genuine advantage over shorting the stock. But the borrow cost has not vanished — it is priced *into the future's discount* by the arbitrageurs who do borrow. You pay it in the basis instead of in a fee.
- **Treating a `REVERSE_CASH_AND_CARRY` signal as executable.** The short spot leg needs shares. In India naked short selling is prohibited and delivery must be honoured at settlement, so the leg depends on the SLB market having the name at the rate you assumed.
- **Rounding the mispricing before comparing it to the threshold.** v1.0.0 rounded to two decimals first, so a 0.2996% edge became 0.30% and fired a signal that does not cover its own costs.
- **Screening on a threshold that ignores transaction taxes.** In India STT and stamp duty apply to both legs and to physical settlement at expiry; they routinely exceed a 0.3% round-trip assumption on their own.
- **Assuming the contract is listed.** No US venue listed single stock futures between 18 September 2020 and 27 July 2026. A universe file inherited from before or across that gap will price contracts that did not exist.

## Verification

- Band collapse: zero borrow fee and zero lending income, $S = 2500$, $r = 6\%$, $T = 30/365$ $\implies$ ceiling = floor = `theoretical_fair_value` = $2500 e^{0.06 \cdot 30/365}$ = **2512.359217**.
- Band asymmetry: a 0.5% borrow fee $\implies$ ceiling unchanged at 2512.359217, floor at $2500 e^{0.055 \cdot 30/365}$ = **2511.326953**. Contracted 0.5% lending income $\implies$ ceiling drops to the floor.
- Hard-to-borrow regression against v1.0.0: 20% borrow fee, market 2495.00 $\implies$ band $[2471.397753, 2512.359217]$, signal `NEUTRAL`, `gross_edge_pct == 0.0` (v1.0.0 reported `CASH_AND_CARRY` at +0.95%).
- Threshold precision regression: a gross edge of 0.2996% against a 0.30% threshold $\implies$ `NEUTRAL`; 0.3001% $\implies$ `CASH_AND_CARRY`; exactly 0.300% $\implies$ `CASH_AND_CARRY` (inclusive).
- Signal arithmetic: market 2530.00 against a 2512.359217 ceiling $\implies$ `CASH_AND_CARRY`, `gross_edge_pct` = **0.7022%**. Market 2495.00 against a 2511.326953 floor $\implies$ `REVERSE_CASH_AND_CARRY`, `gross_edge_pct` = **−0.6501%**, with the located-borrow warning in `audit_notes`.
- Dividends: 20.00 at day 15, $r = 6\%$ $\implies$ `dividend_pv` = $20 e^{-0.06 \cdot 15/365}$ = **19.950746**, ceiling **2492.309841**, floor **2491.285814**. Ex-date at day 45 on a 30-day contract $\implies$ excluded, `excluded_dividends == 1`, band unchanged. Ex-date at day 30 $\implies$ included. $\text{PV}(D) \ge S$ $\implies$ `SSFInputError`.
- Input rejection: NaN, Inf, zero, negative, boolean and string prices; `risk_free_rate_annual = 6.0`; negative borrow or lending rate; lending income above the borrow fee; `lot_size` of 0, negative or float; `days_to_expiry` of −1, 30.0 or 4000; a string `settlement_type`; a 252.0 day count — each raises `SSFInputError` rather than returning a report.
- Margin gating: an `NSE` or `EUREX` spec with no explicit percentages $\implies$ `SSFConfigError` (v1.0.0 silently applied 15%/50% and reported 3.33x). A `CME` spec $\implies$ 15% / 50% defaults, leverage 3.3333x, `margin_basis` naming CFTC Rule 41.45. Percentages outside $(0, 1]$ $\implies$ rejected.
- Ex-dividend gate: 20.00 on a 2500.00 stock is 0.8% $\implies$ `is_adjusted` `False`, base price unchanged at 2500.00 (v1.0.0 returned 2480.00). 60.00 is 2.4% $\implies$ adjusted to 2440.00. Exactly 2.0% $\implies$ adjusted (inclusive). Adjustment applies to the futures settlement price: 2515.00 previous settlement less a 60.00 extraordinary dividend $\implies$ 2455.00. A missing `underlying_market_price` $\implies$ `SSFInputError`, never an assumed extraordinary dividend.
- Boundary: `days_to_expiry = 0` $\implies$ band collapses to the spot price. `day_count_basis = 360` $\implies$ ceiling $2500 e^{0.06 \cdot 30/360}$.
- Run `python -m unittest discover -s skills/single-stock-futures-where-available/scripts`.

## Related Skills

- `short-selling-borrow-cost-and-availability-modeling`
- `corporate-action-event-calendar-integration`
- `futures-contract-roll-automation`
- `physical-vs-cash-settlement-handling`
- `synthetic-continuous-futures-contract-construction`
- `us-reg-sho-short-sale-locate-requirements`
- `india-sebi-algo-trading-tagging-requirements`
- `options-margin-span-calculation-global`
- `total-return-swap-synthetic-exposure`
