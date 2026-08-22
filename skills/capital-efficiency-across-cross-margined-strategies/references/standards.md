# Cross-Margin Offsets: Methodology and Sources

Everything below is scoped to a named clearing house, regulator or broker. Margin
methodologies are set by the clearing organisation and revised on its schedule — none of
these parameters are universal, and all of them change. Verify against the current
primary source before relying on any figure here.

## 1. What the model computes

| Quantity | Definition |
|---|---|
| Isolated margin | Sum of standalone requirements, `IM = sum(M_i)`, all `M_i >= 0`. |
| Spread credit | `min(remaining_long, remaining_short) x credit_rate`, consumed from both legs. |
| Cross margin | `CM = max(IM - sum(credits), IM x min_cross_margin_fraction)`. |
| Capital Efficiency Ratio | `CER = IM / CM`, defined as 1.0 for an empty book. |

**`CER` is bounded above by 2.0.** Each spread consumes its credited amount from both
legs, so total credit cannot exceed `IM / 2`. This is a property of the estimator, not of
margining — scenario engines net further.

## 2. Offset credits are published parameters, not computed statistics

**CME SPAN — inter-commodity spread credits.** Each exchange defines a table of recognised
spread formations, the leg ratio, and the percentage credit. The credit is applied to the
smaller of the two leg values, and SPAN assigns each spread a **priority** determining
which spreads form first. Published examples include Corn vs Soybeans at a 1:2 ratio with
a 65% credit rate. The total requirement is scan risk plus intra-commodity spread charges
plus delivery risk, less inter-commodity credits, floored at the short option minimum.
Credit rates are set by the exchange from its own historical price analysis — they are not
read off a customer's correlation matrix.
Source: CME Group, *SPAN Methodology Overview* — https://www.cmegroup.com/solutions/risk-management/performance-bonds-margins/span-methodology-overview.html

This is why the module ranks spreads highest-credit-first and offers
`credit_rate_overrides`: the published rate is the correct input where it exists, and the
correlation matrix is only a stand-in.

## 3. US portfolio margin (FINRA Rule 4210(g) / OCC TIMS)

- Portfolio margin under **FINRA Rule 4210(g)** is a risk-based alternative to
  strategy-based margin, using a methodology derived from the OCC's **Theoretical
  Intermarket Margining System (TIMS)**.
- **Minimum equity**: $100,000 with full real-time intraday monitoring, $150,000 with
  partial monitoring, $500,000 without either; $5 million for certain non-broker-dealer
  customers.
  Source: FINRA, *2024 Annual Regulatory Oversight Report — Portfolio Margin and Intraday
  Trading* — https://www.finra.org/rules-guidance/guidance/reports/2024-finra-annual-regulatory-oversight-report/portfolio-margin-intraday-trading
  and FINRA Rule 4210 — https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210
- **It is a scenario model.** The portfolio is revalued at a grid of underlying price
  points and the requirement is the worst loss: approximately +/-15% for individual
  equities and sector indexes, +/-10% for non-high-cap broad-based indexes, and -8%/+6%
  for high-cap broad-based indexes.
- **Offsets are group-based, and many groups get none.** Classes are collected into class
  groups and product groups with fixed offset percentages — up to 90% between broad-based
  index class groups. **Non-index single-stock positions receive no P&L offset at all.**
- **Per-contract minimum**: $0.375 multiplied by the contract multiplier for every option,
  future or warrant carried long or short.
  Source: Cboe, *Portfolio Margining* (Cboe Rule 10.4, OCC TIMS) —
  https://www.cboe.com/us/options/portfolio_margining_rules/ and OCC, *Customer Portfolio
  Margin* — https://www.theocc.com/risk-management/customer-portfolio-margin

Implication for this module: for a US single-name equity book, the correlation proxy is
simply wrong — pass `credit_rate_overrides` of `0.0`. For options books, the per-contract
minimum is a floor this model cannot see; approximate it with
`min_cross_margin_fraction`.

## 4. Broker implementations

- **Interactive Brokers**: applies TIMS nightly to US stocks, OCC stock and index options
  and US single stock futures. Portfolio margin requires **$110,000 to initiate and
  $100,000 to maintain**; below $100,000 a surcharge transitions the account back toward
  Reg T levels.
  Source: IBKR, *Portfolio Margin Eligibility* —
  https://www.interactivebrokers.com.au/en/trading/marginRequirements/marginPortfolio.php
- **Bybit Unified Trading Account**: portfolio margin derives offsets from **stress
  testing** of mark price and implied volatility, hedging across USDT, USDC and inverse
  derivatives and spot **within the same account**.
  Source: Bybit, *Margin Calculations under Portfolio Margin* —
  https://www.bybit.com/en/help-center/article/Margin-Calculations-Under-Portfolio-Margin
- **Delta Exchange**: offers a portfolio-margin mode for crypto derivatives.
  Source: Delta Exchange, *Margin Explainer* —
  https://guides.delta.exchange/delta-exchange-india-user-guide/trading-guide/margin-explainer

## 5. Offsets do not cross clearing organisations by default

Positions at two venues are two accounts at two clearing organisations and do not net.
Netting between clearing houses exists only under a formal cross-margin programme
negotiated between them — the CME–FICC arrangement covering Treasury securities against
CME interest rate futures, for example, launched for house accounts of common clearing
members and extended toward customer accounts subsequently. These programmes have
eligibility conditions and are not available by simply holding two accounts.
Source: CME Group, *CME-FICC Cross-Margin Program* —
https://www.cmegroup.com/solutions/clearing/cme-ficc-cross-margin-program.html

For that case use the sibling skill `cross-margining-across-asset-classes`.

## 6. On the correlation haircut

The `correlation_haircut` default of 0.80 is a **conventional conservatism choice made by
this module, not a published standard**. No regulator or clearing house prescribes a
percentage haircut to apply to a customer's own correlation estimates — real engines do
not accept customer correlations as an input at all. Earlier versions of this document
asserted a "20–30% haircut" as an engineering standard; no primary source supports that,
and the claim has been withdrawn. Treat the haircut as documenting how much you distrust
your own correlation estimate, and prefer a published credit rate wherever one exists.
