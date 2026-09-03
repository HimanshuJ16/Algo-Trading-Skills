# Warrants & Structured Product Standards

Every formula and market convention below is sourced. Where a convention is an
issuer/market practice rather than a rule, it is labelled as such.

## 1. Product classification

| Product | Issuer | Payoff structure | Barrier | Dilution |
| :--- | :--- | :--- | :--- | :--- |
| Covered call warrant | Investment bank | Vanilla European call | None | None |
| Covered put warrant | Investment bank | Vanilla European put | None | None |
| Turbo Bull / Bull CBBC | Investment bank | **Down-and-out** call, always in the price | Call price $B \ge K$ | None |
| Turbo Bear / Bear CBBC | Investment bank | **Up-and-out** put, always in the price | Call price $B \le K$ | None |
| Equity (subscription) warrant | Corporate issuer | Call on newly issued stock | None | **Dilutive** — not modelled by this skill |

A turbo warrant "is a barrier option of the down and out type"; a CBBC "is a
knockout barrier option; if the price of the underlying asset reaches the call
price at any time prior to its maturity date, the CBBC is called back by its
issuer, and trading of the CBBC is terminated immediately."

## 2. Entitlement ratio — the exchange quotes the reciprocal

HKEX defines the entitlement ratio as **"the number of products required to be
converted into a unit of the underlying asset at the strike price on the expiry
date"** — i.e. *warrants per share*. Issuer education material uses "conversion
ratio, also known as warrants per share" for the same quantity.

This skill's `entitlement_ratio` is $R_{\text{ent}}$ = **shares per warrant** =
$1 / \text{conversion ratio}$:

$$R_{\text{ent}} = \frac{1}{\text{conversion ratio quoted by the exchange}}$$

A term sheet reading "Entitlement ratio: 10" is $R_{\text{ent}} = 0.1$. Entering
`10.0` scales price, delta and hedge size by $100\times$ with no error raised.
Use `entitlement_ratio_from_conversion_ratio()`.

## 3. Covered warrant valuation — Black-Scholes-Merton

With continuous dividend yield $q$, all option-level quantities then multiplied
by $R_{\text{ent}}$:

$$d_1 = \frac{\ln(S/K) + \left(r - q + \tfrac{1}{2}\sigma^2\right)T}{\sigma\sqrt{T}}, \qquad d_2 = d_1 - \sigma\sqrt{T}$$

$$C = Se^{-qT}N(d_1) - Ke^{-rT}N(d_2), \qquad P = Ke^{-rT}N(-d_2) - Se^{-qT}N(-d_1)$$

$$\Delta_{\text{call}} = e^{-qT}N(d_1), \qquad \Delta_{\text{put}} = -e^{-qT}N(-d_1)$$

$$\Gamma = \frac{e^{-qT}\varphi(d_1)}{S\sigma\sqrt{T}}, \qquad \text{Vega} = Se^{-qT}\sqrt{T}\,\varphi(d_1)$$

$$\Theta_{\text{call}} = -\frac{Se^{-qT}\varphi(d_1)\sigma}{2\sqrt{T}} + qSe^{-qT}N(d_1) - rKe^{-rT}N(d_2)$$

$$\Theta_{\text{put}} = -\frac{Se^{-qT}\varphi(d_1)\sigma}{2\sqrt{T}} - qSe^{-qT}N(-d_1) + rKe^{-rT}N(-d_2)$$

**The two theta terms that are easy to get wrong.** Call theta discounts at
$N(d_2)$, not $N(d_1)$; put theta **adds** the rate term rather than subtracting
it. Version 1.1.0 of this skill made both mistakes and overstated put time decay
by roughly 60% at a typical 90-day, 25%-vol strike.

Reported units: $\Theta$ per **calendar day** (annual $/365$), Vega per **one
volatility point** (per-unit $/100$).

## 4. Turbo / CBBC valuation — the issuer convention

CBBCs are **not** marked with Black-Scholes. HK issuer and regulator education
material is explicit on all three points:

- **Price.** "CBBCs' prices are generally higher than the calculated intrinsic
  values, mainly because certain funding costs are included." The HK Investor and
  Financial Education Council's worked example prices a bull contract as the
  index level "less the strike price, divided by the entitlement ratio, plus the
  finance cost".

$$P_{\text{bull}} = R_{\text{ent}}\left[(S - K)^{+} + K \cdot f \cdot \frac{n}{365}\right], \qquad P_{\text{bear}} = R_{\text{ent}}\left[(K - S)^{+} + K \cdot f \cdot \frac{n}{365}\right]$$

  where $f$ is the issuer funding rate from the launch announcement and $n$ the
  days remaining. Dividing by the exchange-quoted conversion ratio is the same
  operation as multiplying by $R_{\text{ent}}$.

- **Delta.** "All CBBCs are in-the-price products with a Delta of approximately 1
  (Delta One Product)"; their prices "closely mimic price changes of the
  underlying asset". Hence $\Delta_{\text{raw}} = \pm 1$ and
  $\Delta_{\text{warrant}} = \pm R_{\text{ent}}$.

- **Volatility.** "Benefiting from long deep in-the-money feature, CBBC is
  theoretically less affected by the implied volatility, not as the warrants."

$$\Theta_{\text{CBBC}} = -\frac{K \cdot f \cdot R_{\text{ent}}}{365} \text{ per calendar day (the funding accrual)}$$

**Known limitation of this convention.** It yields $\Gamma = \text{Vega} = 0$. The
true product is a barrier option whose convexity is concentrated at the call
price. Size barrier-proximity risk from the distance to the call price and the
MCE monitor, not from these Greeks.

## 5. Mandatory Call Event (MCE)

| | Category N | Category R |
| :--- | :--- | :--- |
| Call price vs strike | Call price **equals** the strike | Bull: call price **above** strike; bear: **below** |
| Buffer | None | Yes |
| Payout on MCE | **Nothing** — entire investment lost | Residual value, possibly zero |

Trigger (inclusive — touching the call price calls the contract):

$$\text{Bull: } S \le B \qquad \text{Bear: } S \ge B$$

Residual value, calculated **against the strike price, not the call price**:

$$V_{\text{res, bull}} = \max\!\left(0,\; S_{\text{settle}} - K\right) R_{\text{ent}}, \qquad V_{\text{res, bear}} = \max\!\left(0,\; K - S_{\text{settle}}\right) R_{\text{ent}}$$

$S_{\text{settle}}$ is fixed over the **MCE valuation period**, not at the
triggering tick: for a bull contract it is the *lowest* underlying price in the
session during which the CBBC was called and in the following session. A residual
estimated from the trigger tick is therefore an **upper bound** on what settles.

## 6. Gearing — HKEX definitions

HKEX: "'Gearing' means the relationship that the cost of the underlying asset
bears to the cost of a derivative warrant"; "'Effective gearing' of a derivative
warrant is calculated by multiplying the gearing and the delta of the derivative
warrant". Issuer material states it as formulas:

$$\text{Gearing} = \frac{S}{P_{\text{warrant}} \times \text{conversion ratio}} = \frac{S \cdot R_{\text{ent}}}{P_{\text{warrant}}}, \qquad \text{Effective Gearing} = \text{Gearing} \times \Delta$$

The unambiguous reading — and the identity the implementation is tested against —
is that effective gearing is the **price elasticity** of the warrant:

$$\text{Effective Gearing} = \frac{S}{P_{\text{warrant}}} \cdot \frac{\partial P_{\text{warrant}}}{\partial S} = \frac{S \cdot R_{\text{ent}}}{P_{\text{warrant}}} \times \left|\Delta_{\text{raw}}\right|$$

$R_{\text{ent}}$ appears **once**. Multiplying an $R_{\text{ent}}$-scaled gearing
by an $R_{\text{ent}}$-scaled delta squares it. For a CBBC $|\Delta_{\text{raw}}| = 1$,
so effective gearing equals simple gearing — the source of a CBBC's stable,
advertised leverage.

Gearing is a statistic of a **traded** price. Computing it against a theoretical
price that sits below the exchange minimum tick produces a figure that describes
nothing tradable; pass `market_price` instead.

## 7. Delta hedging

$$\text{Warrant book delta} = N_{\text{warrants}} \times \Delta_{\text{warrant}}, \qquad \text{Target underlying} = -\,N_{\text{warrants}} \times \Delta_{\text{warrant}}$$

$$\text{Net rebalance} = \text{Target underlying} - \text{Current hedged shares}$$

$N_{\text{warrants}}$ is **signed**: positive long, negative issued. A long
call-warrant book is hedged short the underlying; an issued one is hedged long.

**Knock-out discontinuity.** On an MCE, $\Delta_{\text{warrant}} \to 0$
instantaneously while the underlying hedge does not. The target becomes zero
shares and the entire hedge must be unwound in one instruction; anything left is
an outright directional position opened at the worst point of the session.

## 8. Regulatory and market-structure touchpoints

These are context for the desk, not obligations discharged by this engine:

- **Non-collateralised issuance.** HK covered warrants and CBBCs are issued as
  non-collateralised structured products — unsecured obligations of the issuer.
  Issuer credit risk is not modelled here; see
  `counterparty-credit-risk-for-otc-derivatives`.
- **Liquidity provision.** Warrant and CBBC issuers act as designated liquidity
  providers on their own lines, which is why market-maker delta hedging is the
  dominant use case for this skill.
- **Board lots.** Warrants and CBBCs trade in exchange-defined board lots, and so
  do their underlyings. Set `rebalance_threshold_shares` accordingly; see
  `minimum-fill-size-and-lot-rounding-logic`.
- **Pre-trade risk controls.** Hedge orders generated here are ordinary equity
  orders and remain subject to the desk's pre-trade risk layer (SEC Rule 15c3-5,
  MiFID II RTS 6 where applicable). The engine has no order-routing authority.

## 9. Sources

| Claim | Source |
| :--- | :--- |
| Gearing, effective gearing, delta, entitlement ratio definitions | HKEX FAQ, "Pricing Parameters and Ratios" (Derivative Warrants product pricing) — <https://www.hkex.com.hk/Global/Exchange/FAQ/Products/Securities/DW/Product-Pricing/Pricing-Parameters-and-Ratios?sc_lang=en> |
| Gearing formula with conversion ratio; "conversion ratio, also known as warrants per share" | Macquarie HK warrants education, "Warrant tutorial" — <https://www.warrants.com.hk/en/education/warrant_tutorial06> |
| Category N vs R, MCE, residual value, CBBC structure | HKEX, "Introduction to Callable Bull / Bear Contracts" (Feb 2025 product sheet) — <https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/Structured-Products/Product-Sheet/2025-Feb/HKEX_CBBC_infosheet_en.pdf> |
| Funding cost in the CBBC price; residual value calculated on the strike price; MCE valuation period is the lowest price of the calling session and the next | HK Investor and Financial Education Council (IFEC), "A tricky derivative for the market's ups and downs" — <https://www.ifec.org.hk/web/en/investment/investment-products/warrants/basics/a-tricky-derivative-for-the-markets-ups-and-downs.page> |
| CBBC delta ≈ 1 (Delta One Product); reduced implied-volatility sensitivity; knockout barrier structure | Tiger Brokers HK product documentation, "Bull/Bear contracts"; HSBC, "Callable Bull/Bear Contracts (CBBC) Driving Investment Power" handbook |
| Turbo warrant is a down-and-out barrier option; barrier and strike commonly coincide | "Turbo warrant", Wikipedia — <https://en.wikipedia.org/wiki/Turbo_warrant> |
| Black-Scholes-Merton prices, Greeks and theta with dividend yield | Hull, *Options, Futures, and Other Derivatives*, ch. 15 & 19; M. Haugh, Columbia IEOR "The Black-Scholes Model" — <https://www.columbia.edu/~mh2078/FoundationsFE/BlackScholes.pdf>; Macroption, "Black-Scholes Formula" — <https://www.macroption.com/black-scholes-formula/> |
