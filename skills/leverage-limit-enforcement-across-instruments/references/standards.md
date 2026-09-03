# Risk Management Standards — leverage-limit-enforcement-across-instruments

## Engineering defaults

Every value below is a **house-policy default shipped by this module**, not a
regulatory prescription. The "Anchored to" column states what the number was
chosen against; it does not make the number mandatory. Map each to your firm's
mandate, prospectus, and applicable regime before going live.

| Parameter | Default | Anchored to |
|---|---|---|
| `max_gross_leverage` | $3.0\times$ | AIFMD's *substantial leverage* threshold (see below), used here as a conservative internal ceiling |
| `max_net_leverage` | $1.5\times$ | House policy. Must be strictly below `max_gross_leverage` or the gate can never bind, since $L_{\text{net}} \le L_{\text{gross}}$ |
| `asset_class_limits["EQUITY"]` | $2.0\times$ | Reg T initial margin (50% $\Rightarrow$ 2:1) |
| `asset_class_limits["CRYPTO"]` | $3.0\times$ | House volatility cap. **Exceeds** the EU retail CFD limit of 2:1 — see below |
| `asset_class_limits["FX"]` | $10.0\times$ | House cap for major pairs; tighter than the EU retail CFD limit of 30:1 |
| `asset_class_limits["FUTURES"]` | $5.0\times$ | House cap. Exchange SPAN / clearing margin is the binding operational constraint, not this number |
| `default_asset_class_limit` | `None` (fail closed) | An unconfigured asset class is rejected rather than given an unchosen cap |
| `LIMIT_RELATIVE_TOLERANCE` | $10^{-9}$ | Float-representation slack only. Comparisons run on unrounded ratios |

## Regulatory & operational notes

**United States — pre-trade rejection is mandatory for market-access broker-dealers.**
SEC [Rule 15c3-5](https://www.federalregister.gov/documents/2010/11/15/2010-28303/risk-management-controls-for-brokers-or-dealers-with-market-access)(c)(1)(i)
requires controls reasonably designed to prevent entry of orders exceeding pre-set
credit or capital thresholds "in the aggregate for each customer and the
broker-dealer, and where appropriate more finely-tuned by sector, security, or
otherwise," applied on an automated pre-trade basis before routing. Post-trade
review alone does not satisfy it. Applies to broker-dealers with market access
(including sponsored access they provide); a proprietary firm trading through a
third-party BD is covered indirectly, via that BD's controls.

**United States — Reg T and FINRA 4210 bound *margin*, not this ratio.**
[12 CFR 220.12(a)](https://www.ecfr.gov/current/title-12/chapter-II/subchapter-A/part-220/section-220.12)
sets initial margin for a margin equity security at 50% of current market value
(or the higher percentage set by the regulatory authority where the trade occurs),
which is where the $2.0\times$ EQUITY default comes from. FINRA Rule 4210(c)
maintenance margin is 25%, permitting roughly $4{:}1$ at maintenance level, and
portfolio margin under Rule 4210(g) permits more still. A book inside the
$2.0\times$ cap is therefore *tighter* than what the margin regime allows — the
leverage cap and the liquidation threshold are separate numbers.

Currency note: the SEC approved FINRA's replacement of the day-trading margin
provisions (including the "pattern day trader" definition and its $25{,}000$
minimum equity requirement) with an intraday margin standard on 14 April 2026,
effective 4 June 2026 with an 18-month phase-in ending 20 October 2027. That
change does **not** affect the Rule 4210(c) maintenance requirements above.

**EU — AIFMD defines the two leverage measures this module resembles.**
Commission Delegated Regulation (EU) No
[231/2013](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32013R0231)
Art. 7 (*gross method*) defines exposure as the sum of the absolute values of all
positions, excluding cash and cash equivalents held in the AIF's base currency,
with derivatives converted to an equivalent underlying position per Annex II —
for a plain vanilla equity option, `contracts × notional contract size × market
value of the underlying × delta`. Art. 8 (*commitment method*) applies the same
conversion but permits netting and hedging arrangements.

Two consequences for how this module's output may be described:

- This module's **gross** measure is the AIFMD gross-method *shape*, but it does
  not implement the cash / cash-equivalent exclusion of Art. 7. Do not submit its
  output as an Annex IV figure without that adjustment.
- This module's **net** measure ($|\sum E_i|$) nets everything against everything
  and is **not** the commitment method, which nets only duly-verified hedging and
  netting arrangements. Never report one as the other.

[Art. 111(1)](https://www.legislation.gov.uk/eur/2013/231/article/111) provides
that leverage is employed "on a substantial basis" when commitment-method
exposure exceeds **three times NAV** — a supervisory reporting trigger under
AIFMD Art. 24(4), not a hard cap. The $3.0\times$ default here borrows that
threshold as a conservative internal ceiling; it is not a legal limit on any
non-AIF trading entity.

**EU/UK retail CFDs — a hard cap that overrides the defaults where it applies.**
ESMA's 2018 product-intervention measures capped retail CFD opening leverage at
30:1 (major FX), 20:1 (non-major FX, gold, major indices), 10:1 (other
commodities, non-major indices), 5:1 (individual equities) and **2:1
(cryptocurrencies)**. The temporary ESMA measures expired 31 July 2019 and were
replaced by permanent national measures adopted by NCAs under
[MiFIR Art. 42](https://www.esma.europa.eu/press-news/esma-news/esma-ceases-renewal-product-intervention-measures-relating-contracts).
**The shipped `CRYPTO` default of $3.0\times$ exceeds the 2:1 retail crypto
limit**: any operation facing EU/UK retail clients must lower it. The limits do
not apply to professional clients or eligible counterparties.

**Everywhere — this is an exposure control, not a margin control.** Nothing here
models initial margin, maintenance margin, SPAN, cross-margin offsets, funding, or
liquidation price. Run it alongside `margin-utilization-circuit-breaker`.
