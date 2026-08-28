# Standards for Scenario-Based Stress Testing with Custom Shocks

## What actually binds whom

Nothing in this skill implements a regulator-set methodology, and no regulator prescribes
a scenario stress test of this shape for a proprietary algorithmic trading firm. Two
sources are commonly cited in this area and both are routinely misapplied:

| Source | Who it binds | What it actually requires | Status |
|---|---|---|---|
| MiFID II RTS 6, [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng), **Article 10 "Stress testing"** | EU investment firms engaged in algorithmic trading | A **systems capacity** test, as part of the annual self-assessment under Article 9: that the trading systems and the Article 12–18 controls "can withstand increased order flows or market stresses", run as high-messaging-volume and high-trade-volume tests at twice the previous six months' peak, and carried out so as not to affect the production environment. | Mandatory, in force |
| BCBS, [*Stress testing principles* (d450)](https://www.bis.org/bcbs/publ/d450.htm), October 2018 | Banks and their supervisors in BCBS member jurisdictions | Nine high-level principles for a stress testing framework. Explicitly: "These principles do not constitute Standards … Instead the principles are Guidelines". | Guidance, not a standard; not addressed to prop traders |

**Article 10 is not a portfolio P&L requirement.** Its title invites the confusion, but
the test it mandates is about message throughput and order volume, not about what the
book loses in a crash. Do not cite it as authority for the numbers this engine produces.

Where BCBS d450 *is* useful is as a design discipline, and two of its statements bear
directly on how this engine behaves:

- Principle 4 — "Stress testing frameworks should capture material and relevant risks and
  apply stresses that are sufficiently severe": *"key variables within each scenario
  should be internally consistent"*, and *"If certain material and relevant risks are
  excluded from the scenarios, their exclusion should be explained and documented."*
  That is why unshocked positions are reported (`unshocked_asset_ids`,
  `factors_never_shocked`, `status`) rather than silently contributing zero.
- Principle 4, on scenario design — *"Scenarios not based on historical events and
  empirically observed relationships may be warranted for some or all risks if new or
  heightened vulnerabilities are identified, or if historical data do not contain a severe
  crisis episode."* That is the case for `CUSTOM_HYPOTHETICAL` scenarios: a historical
  replay is a floor, not a ceiling.
- Reverse stress testing is described there as scenarios that "could potentially lead
  banks to fail". This engine does **not** solve for such a scenario;
  `StressScenarioCategory.REVERSE_STRESS` is a caller-applied label only.

## The predefined scenarios, and how they compare to the episodes

These magnitudes are **library defaults**. They are not reconstructions of the episodes,
they are not regulator-set, and where they are milder than the historical record that is
stated below so the gap is a choice rather than an accident. Recalibrate them to the book
you actually run.

| Scenario id | Factor | Shock | Type |
|---|---|---|---|
| `SCEN_2008_LEHMAN` | `EQUITY_SPOT` | −35.0% | relative return |
| | `IMPLIED_VOL` | +150.0% | relative return |
| | `CREDIT_SPREAD` | +300 bp | yield/bps |
| `SCEN_2020_COVID` | `EQUITY_SPOT` | −30.0% | relative return |
| | `CRUDE_OIL` | −60.0% | relative return |
| | `IMPLIED_VOL` | +120.0% | relative return |
| `SCEN_2022_RATES` | `INTEREST_RATE_BPS` | +200 bp | yield/bps |
| | `TECH_GROWTH_SPOT` | −25.0% | relative return |

Sign conventions: a relative shock applies $V \cdot \beta \cdot \Delta$; a yield/bps shock
applies $-V \cdot D \cdot \Delta/10^4$, so a **rise** in a rate or spread is a **loss** for
a long position.

### Where the defaults sit against the record

- **Equity.** The S&P 500's peak-to-trough decline was **−56.8%** over the 2007–2009 bear
  market (October 2007 to March 2009), **−33.9%** over the February–March 2020 pandemic
  bear market, and **−25.4%** over the January–October 2022 tightening bear market
  (Winthrop Wealth, *S&P 500 Bear Markets*, January 2023, source: Bloomberg). The −35% and
  −30% defaults are therefore materially milder than 2008 and slightly milder than 2020;
  the −25% used for `TECH_GROWTH_SPOT` in 2022 is close to the index figure, though the
  Nasdaq-100 fell considerably further that year.
- **Implied volatility.** VIX's all-time closing high is **82.69 on 16 March 2020**; the
  2008 high was **80.86 on 20 November 2008** (Cboe data, tabulated by Macroption). Both
  represent multiples of the pre-crisis level far above the +150%/+120% defaults here.
- **Credit.** The ICE BofA US High Yield index OAS (FRED `BAMLH0A0HYM2`) widened by well
  over 1,000bp between the pre-Lehman weeks of September 2008 and its December 2008 peak.
  The +300bp default is a moderate widening, not a Lehman replay. Published peak figures
  differ across secondary sources; pull the series from FRED and calibrate against the
  dates you care about rather than adopting a headline number.
- **Rates.** The FOMC raised the federal funds target range by **425bp across 2022**, from
  0–0.25% to 4.25–4.50% (Federal Reserve, FEDS Note, *The Federal Reserve's responses to
  the post-Covid period of high inflation*, 14 February 2024). The +200bp default is
  roughly half the policy move; the appropriate shock depends on the tenor your book is
  actually exposed to, which is not the policy rate.
- **Crude.** The −60% default cannot represent April 2020. The NYMEX WTI May 2020 contract
  settled at **−$37.63 on 20 April 2020**, the first negative settlement in the contract's
  history (CFTC, *Interim Staff Report: Trading in NYMEX WTI Crude Oil Futures Contract
  Leading up to, on, and around April 20, 2020*, 24 November 2020). A multiplicative shock
  is bounded at −100% and the engine rejects anything below it; model that episode by
  shocking the position value directly.

## Model conventions this skill relies on

- **Duration.** $\Delta P / P \approx -D_{\text{mod}} \, \Delta y$, first order, ignoring
  convexity. Convexity makes the linear estimate conservative for a long bond on a large
  rate rise and optimistic on a large fall. For credit, substitute spread duration and
  $\Delta s$.
- **Factor beta.** For relative shocks, $\beta$ is the position's return elasticity to the
  factor. Betas estimated on calm-period data are the wrong betas for a crisis scenario —
  that is a known limitation of the approach, not of this implementation.
- **Single period.** One instantaneous revaluation. No path, no rebalancing, no funding
  cost, no liquidation cost.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 10 — <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>
- BCBS, *Stress testing principles*, d450, October 2018 — <https://www.bis.org/bcbs/publ/d450.htm>
- CFTC, *Interim Staff Report: Trading in NYMEX WTI Crude Oil Futures Contract Leading up to, on, and around April 20, 2020*, 24 November 2020 — <https://www.cftc.gov/PressRoom/PressReleases/8315-20>
- Federal Reserve, FEDS Note, *The Federal Reserve's responses to the post-Covid period of high inflation*, 14 February 2024 — <https://www.federalreserve.gov/econres/notes/feds-notes/the-federal-reserves-responses-to-the-post-covid-period-of-high-inflation-20240214.html>
- Winthrop Wealth, *S&P 500 Bear Markets* (Bloomberg data), January 2023 — <https://winthropwealth.com/wp-content/uploads/2023/01/SP-500-Bear-Markets-CQ.pdf>
- Macroption, *VIX All-Time Highs* (Cboe data) — <https://www.macroption.com/vix-all-time-high/>
- FRED, ICE BofA US High Yield Index Option-Adjusted Spread (`BAMLH0A0HYM2`) — <https://fred.stlouisfed.org/series/BAMLH0A0HYM2>
