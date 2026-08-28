# Standards for Stress Testing Against Historical Crash Scenarios

## What actually binds whom

Nothing in this skill implements a regulator-set methodology, and no regulator prescribes
a historical crash replay of this shape for a proprietary algorithmic trading firm.
Version 1.0.0 of this file stated that the skill "aligns with Basel III stress-testing
frameworks, SEC Rule 15c3-5, and ESMA guidelines on algorithmic trading risk controls."
None of the three supports the claim as written.

| Source | Who it binds | What it actually requires | Status |
|---|---|---|---|
| [17 CFR § 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5) (SEC Market Access Rule) | US broker-dealers with market access, and those providing it to others | **Pre-trade order controls**, not portfolio scenario analysis. §(c)(1)(i) requires controls to "prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds"; §(c)(1)(ii) to "prevent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters". Plus regulatory controls under §(c)(2), an annual review, and an annual CEO certification. | Mandatory, in force. **Contains no stress-testing requirement.** |
| MiFID II RTS 6, [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng), **Article 10 "Stress testing"** | EU investment firms engaged in algorithmic trading | A **systems capacity** test within the Article 9 annual self-assessment: that the trading systems and controls "can withstand increased order flows or market stresses", run as high-messaging-volume and high-trade-volume tests at twice the previous six months' peak. | Mandatory, in force. **Not a portfolio P&L requirement.** |
| BCBS, [*Stress testing principles* (d450)](https://www.bis.org/bcbs/publ/d450.htm), October 2018 | Banks and their supervisors in BCBS member jurisdictions, applied proportionately | Nine high-level principles for a stress testing framework — objectives, governance, policies, methodology, resources, documentation. | Guidance, not a standard; not addressed to prop traders. |

"Basel III" is frequently invoked here and is the loosest of the three: the Basel market
risk framework's stressed-VaR and FRTB requirements bind internationally active **banks**
through their national supervisors. They do not reach a non-bank trading firm, and this
engine implements none of their methodology.

Where BCBS d450 *is* useful is as design discipline. Principle 4 — "If certain material
and relevant risks are excluded from the scenarios, their exclusion should be explained
and documented" — is why unpriced, unshocked and `DEFAULT`-priced positions are reported
(`unpriced_symbols`, `unshocked_symbols`, `fallback_symbols`, `status`) instead of
silently contributing zero.

If you operate under a regime that *does* impose portfolio stress testing — a UCITS or
AIFMD manager, a bank, a CCP clearing member facing its clearing house's own scenarios —
that regime's methodology governs, not this one. Establish your jurisdiction first.

## The built-in scenarios, and how they compare to the episodes

These magnitudes are **library defaults for the broad-market proxies listed**, not
reconstructions of the episodes and not regulator-set scenarios. They are deliberately
left at their 1.0.0 values so existing callers' numbers do not move silently; where they
understate the record, that is stated here with sources so the gap is a choice rather than
an accident. Recalibrate from your own point-in-time data.

| Scenario | Window recorded | Basis | `DEFAULT` |
|---|---|---|---|
| `2020_COVID_CRASH` | 2020-02-19 → 2020-03-23 | close-to-close over the S&P 500's peak-to-trough window | −30% |
| `2008_GFC` | 2007-10-09 → 2009-03-09 | close-to-close over the S&P 500's peak-to-trough window | −50% |
| `2015_FLASH_CRASH` | 2015-08-24 (single day) | intraday trough against the prior close — **not** peak-to-trough | −6% |

Shipped shocks, all three scenarios: `SPY`, `QQQ`, `IWM`, `EEM`, `TLT`, `GLD`, `DEFAULT`.

### Where the defaults sit against the record

- **2020 COVID.** The S&P 500 closed at **3386.15 on 19 February 2020** and **2237.40 on
  23 March 2020**, a decline of **−33.9%**. The `SPY: -0.3393` default matches that
  close-to-close move. The Russell 2000's drawdown over the same episode was materially
  deeper than the `IWM: -0.4131` carried here — roughly −44% on published accounts — so
  the small-cap leg is the mildest part of this scenario.
- **2020 COVID, non-equity legs.** `GLD: -0.0380` and `TLT: 0.1560` are *window* returns
  over the equity index's dates. They are not those assets' worst moves inside the
  episode: gold fell approximately **12% between 9 and 19 March 2020** as leveraged
  holders raised cash (World Gold Council). A book holding gold as a crash hedge and
  stressed at −3.8% is flattered roughly threefold on that leg. If the hedge is what you
  are relying on, shock it at its own adverse move.
- **2008 GFC.** Version 1.0.0 labelled this scenario "Sep-Nov 2008" while carrying
  `SPY: -0.5190`, which matches no such window — the S&P 500 closed at **752.44 on
  20 November 2008** against roughly 1280 in early September, about −41%. The window now
  recorded is the bear market the magnitudes sit closer to: **1565.15 on 9 October 2007**
  to **676.53 on 9 March 2009**, **−56.8%**, the largest US equity decline since the
  Second World War. The shipped −51.9% is milder than that and the remaining symbols were
  not independently re-derived for the corrected window. Treat the whole scenario as a
  starting point requiring recalibration.
- **2015 flash crash.** The S&P 500 fell to an intraday **1867.01 on 24 August 2015**,
  **−5.3%** against the 21 August close, and finished the day about **−3.9%**. The
  index-level magnitudes here badly understate what the episode did to *ETFs*, which is
  the relevant risk for a book that holds them: many traded far below net asset value in
  the first hour — the S&P 500 Low Volatility ETF (`SPLV`) printed **−45.8% intraday** and
  closed −5.3% — and **1,237 circuit-breaker halts** fired that day, **85% of them in
  exchange-traded products**. If your book holds ETFs, shock them at the dislocation, not
  at the index. Note also that this scenario's basis differs from the other two: ranking a
  single-day intraday move against a multi-month peak-to-trough on magnitude compares
  different quantities.

### Single-name shocks: why none are shipped

Version 1.0.0 carried per-single-name shocks (`AAPL`, `MSFT`, `AMZN`, `TSLA`, `NVDA`,
`META`) in all three scenarios. Two of them could not have existed:

| Symbol | 1.0.0 `2008_GFC` shock | First traded |
|---|---|---|
| `TSLA` | −80.0% | **29 June 2010** (IPO at $17, Nasdaq) |
| `META` | −50.0% | **18 May 2012** (IPO at $38 as Facebook, Nasdaq) |

Neither security existed during the 2008 window; the returns were fabricated, not
conservative. They have been removed rather than re-estimated, along with the remaining
unsourced single-name shocks.

A library cannot ship correct single-name crash returns for a universe it does not know,
and the attempt is self-defeating: the names available to hard-code are the ones that
survived to the present day, so the resulting vector systematically omits the constituents
that went to −100%. That is precisely the survivorship bias this skill warns about. Build
single-name shocks from point-in-time constituent data — see
`survivorship-bias-free-universe-construction` — and pass them in via `CrashScenario`.

## Engine conventions this skill relies on

- **Per-symbol return replay.** `pnl_i = quantity_i * price_i * shock_i`, linear in
  position value. Quantities are signed; a short is negative.
- **Denominator.** Every percentage is over `portfolio_nav`, the capital base. Net
  exposure is not a capital base.
- **Gate.** `worst_loss_pct >= max_stressed_loss_pct`, evaluated unrounded, where
  `worst_loss_pct` is a loss magnitude floored at zero. At-limit is a breach; a scenario
  gain never fires the gate.
- **Bounded at −100%.** Shocks below −1.0 are rejected; a multiplicative shock cannot take
  a price through zero.
- **Single period.** One instantaneous revaluation. No path, no rebalancing, no margin
  call, no liquidation cost, no correlation model.

## Default parameters

| Parameter | Value | Description |
|---|---|---|
| `DEFAULT_MAX_STRESSED_LOSS_PCT` | 0.15 (15% of NAV) | Library default gate. Not a regulatory limit — calibrate and record why. |
| `FALLBACK_SYMBOL_KEY` | `"DEFAULT"` | Scenario key applied to unnamed symbols; usage is reported in `fallback_symbols`. |
| Built-in scenarios | 3 | `2020_COVID_CRASH`, `2008_GFC`, `2015_FLASH_CRASH` |

## Category

`risk-management`

## Sources

- 17 CFR § 240.15c3-5, Risk management controls for brokers or dealers with market access — <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>
- SEC, *Responses to Frequently Asked Questions Concerning Risk Management Controls for Brokers or Dealers with Market Access* — <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>
- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Articles 9–10 — <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>
- BCBS, *Stress testing principles*, d450, October 2018 — <https://www.bis.org/bcbs/publ/d450.htm>
- Wikipedia, *United States bear market of 2007–2009* (S&P 500 closes: 1565.15 on 2007-10-09, 676.53 on 2009-03-09, −56.8%; 752.44 on 2008-11-20) — <https://en.wikipedia.org/wiki/United_States_bear_market_of_2007%E2%80%932009>
- St. Louis Fed, *How COVID-19 Has Impacted Stock Performance by Industry*, March 2021 (S&P 500 peak 2020-02-19, trough 2020-03-23) — <https://www.stlouisfed.org/on-the-economy/2021/march/covid19-impacted-stock-performance-industry>
- World Gold Council, *Investment Update: Gold prices swing as markets sell off* (gold −12% between 9 and 19 March 2020) — <https://www.gold.org/goldhub/research/gold-prices-swing-as-markets-sell-off>
- ETF.com, *Aug. 24, 2015 Flash Crash Part Of Wall St. History* (S&P 500 intraday 1867.01, −5.3% vs the 21 August close; SPLV −45.8% intraday; 1,237 halts, 85% ETPs) — <https://www.etf.com/sections/features-and-news/aug-24-2015-flash-crash-part-wall-st-history>
- Tesla, *Tesla Announces Pricing of Initial Public Offering*, 28 June 2010 — <https://ir.tesla.com/press-release/tesla-announces-pricing-initial-public-offering>
- Wikipedia, *Initial public offering of Facebook* (18 May 2012) — <https://en.wikipedia.org/wiki/Initial_public_offering_of_Facebook>
