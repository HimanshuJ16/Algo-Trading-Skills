---
name: commodity-futures-storage-and-carry-cost-modeling
description: Quantitative commodity pricing model for calculating theoretical futures
  prices, extracting implied convenience yields, detecting contango vs. backwardation
  regimes, and auditing futures prices against the full-carry no-arbitrage bound.
domain: Derivatives & Pricing
subdomain: Commodity Futures
tags:
- commodity-futures
- cost-of-carry
- convenience-yield
- contango
- backwardation
- storage-cost
brokers_frameworks:
- Generic Derivatives Pricing
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when pricing physical commodity futures (Crude Oil `CL`, Natural Gas `NG`, Gold `GC`, Agriculture `ZC`) or designing term-structure roll strategies. The Cost of Carry model links spot prices ($S_0$) to futures prices ($F_T$) using financing costs ($r$), physical storage/insurance costs ($c$ proportional and/or $U$ per unit), and implied convenience yield ($y$). High convenience yield causes **Backwardation** ($F_T < S_0$), signaling physical inventory scarcity, whereas high storage costs relative to convenience yield lead to **Contango** ($F_T > S_0$).

## When NOT to Use

- **As an arbitrage signal generator on the cheap side of the curve.** For a consumption commodity the cost-of-carry relation is an *inequality*, $F_0 \le (S_0 + U)e^{(r+c)T}$, not an equality. Only the rich side is enforceable. A futures price below your fair-value estimate is a view about convenience yield, not a riskless trade, because you cannot generally borrow and sell short a physical commodity.
- **For non-storable commodities** (electricity, most weather and freight underlyings). Storage arbitrage is the entire basis of this model; without storability the futures price is a risk-neutral expectation, not a carry relation. See `weather-derivatives-and-niche-instrument-handling`.
- **When spot and futures are not the same deliverable.** A refiner's local crude assessment is not the contract-grade deliverable at the delivery point. A basis difference between grades or locations shows up here as a spurious convenience yield or a spurious arbitrage.
- **At sub-daily maturities.** The implied-yield inversion divides by $T$, so within a day or two of expiry ordinary quote noise annualises into implausible yields.

## Prerequisites

- Spot price $S_0$ and futures contract price $F_{market}$ **sampled at the same timestamp**, for the contract-deliverable grade and delivery point.
- Time to maturity $T$ in years, on a stated day-count basis.
- A **continuously compounded** annual financing rate $r$ on that same day-count basis. Money-market quotes (e.g. SOFR, ACT/360, simple) must be converted before use.
- Storage cost as a proportional annual rate $c$, a fixed currency amount per unit per year $U$, or both.

## Workflow

1. **Full-Carry Price (the no-arbitrage bound)**:
   - $F_{full} = (S_0 + U_{PV}) \cdot e^{(r + c) T}$, where $U_{PV}$ is the present value of the fixed per-unit storage charge accruing over $[0, T]$.
   - This is the *upper* bound on the futures price, and equals the price at $y = 0$.
2. **Theoretical Futures Price at an Assumed Yield**:
   - $F_{theoretical} = F_{full} \cdot e^{-yT}$. Because $y$ is unobservable, this is a *view*, not a fair value that arbitrage enforces.
3. **Implied Convenience Yield Extraction**:
   - $y = \frac{1}{T}\ln\left(\frac{F_{full}}{F_{market}}\right)$.
   - If $y < 0$ the bound is violated. Before treating it as profit, re-check timestamp synchronisation, grade/location deliverability, and whether storage and financing costs are understated — those explain the great majority of apparent violations.
4. **Regime Identification**:
   - $F_{market} > S_0$ is `CONTANGO`; $F_{market} < S_0$ is `BACKWARDATION`; equality is `FLAT`. Do not fold the equality case into either regime — a flat curve is a distinct, informative state.
5. **Arbitrage Audit (Cash-and-Carry only)**:
   - Raise a `CASH_AND_CARRY` signal only when $F_{market} > F_{full} \cdot (1 + \text{round-trip costs})$: buy spot, pay financing and storage, sell futures, deliver. This leg is executable by anyone with capital and storage capacity.
   - Do **not** raise an arbitrage on the other side. A cheap futures price is surfaced separately as a reverse-carry *candidate*, actionable only by an existing inventory holder (who is really monetising their own convenience yield) or in a commodity with a genuine lease/borrow market such as gold.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating backwardation as a reverse cash-and-carry arbitrage**: Crude oil routinely trades at implied convenience yields of tens of percent annualised during tight-inventory periods. A model that compares the market price to a fixed "baseline" convenience yield and flags every deviation will fire a false arbitrage on essentially every backwardated market, because the short-physical leg needed to capture it does not exist.
- **Ignoring Convenience Yield ($y$)**: Assuming futures prices are purely driven by $r + c$. In tight physical markets, convenience yield surges, causing deep backwardation that pure storage models fail to explain.
- **Fixed vs. Proportional Storage Costs**: Exchanges regulate physical storage as a fixed charge per unit per day, not as a percentage of spot — CBOT caps grain storage in fractions of a cent per bushel per day and adjusts that cap through the Variable Storage Rate mechanism. Modelling a fixed charge as a percentage of spot silently makes storage cheap when the commodity is cheap, which is exactly backwards.
- **Day-Count / Compounding Misalignment**: $T$ and $r$ must share a day-count basis, and $r$ must be continuously compounded. Pairing an ACT/360 simple money-market quote with an ACT/365 $T$ biases the implied yield by roughly 1.4% of the rate before any market signal is present.
- **Silent NaN propagation**: `nan <= 0` is False, so a naive positivity check passes NaN straight through to `math.log` and returns a NaN price alongside a confidently wrong regime string. Validate for finiteness, not just sign.
- **Non-synchronous quotes**: A settlement-price futures quote against a live spot tick manufactures basis out of nothing. At short maturities the $1/T$ factor amplifies it into a headline-grade convenience yield.

## Verification

- Instantiate `CommodityCarryCostModel(risk_free_rate=0.05, storage_cost_rate=0.02)`. With $S_0 = 100$, $T = 1.0$, $y = 0.01$, verify the theoretical price is $100 \cdot e^{0.06} \approx 106.1837$; with $y = 0.10$, verify $100 \cdot e^{-0.03} \approx 97.0446$. The full-carry price ($y = 0$) must be $100 \cdot e^{0.07} \approx 107.2508$.
- Feed a WTI-like backwardated curve ($S_0 = 80$, $F = 76$, $T = 0.5$). Confirm the regime is `BACKWARDATION`, the implied convenience yield is roughly 17%, and `is_arbitrage_opportunity` is **False** — this is a normal market, not a trade.
- Feed $F_{market} = 115$ against $S_0 = 100$, $T = 1.0$. Confirm `CASH_AND_CARRY`, a negative implied yield, and `convenience_yield_bound_violated`.
- Feed $F_{market} = S_0$ and confirm the regime is `FLAT`, not `BACKWARDATION`.
- Run `python -m unittest discover -s skills/commodity-futures-storage-and-carry-cost-modeling/scripts` and confirm 100% pass rate.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `calendar-spread-and-multi-leg-order-atomicity`
- `futures-contract-roll-automation`
- `physical-vs-cash-settlement-handling`
