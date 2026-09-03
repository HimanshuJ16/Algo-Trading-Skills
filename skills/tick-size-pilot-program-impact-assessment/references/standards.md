# Tick Size Regimes, Spread Metric Standards, and Measured Outcomes

All figures in Section 3 are quoted from the primary sources listed in Section 5. Nothing
in this file is an estimate produced by this skill.

## 1. SEC Tick Size Pilot Program — group structure (historical)

An NMS plan ordered by the Commission on 2014-06-24, submitted by the exchanges and FINRA
("the Participants"), approved 2015-05-06. The quoting and trading requirements ran from
**2016-10-03** until the close on **2018-09-28** (an SEC exemption moved the end date
forward from the scheduled 2018-10-02); post-pilot data collection ran to 2019-04-01. Each
test group held ~400 securities drawn by random stratification on market cap, share price
and consolidated average daily volume; the remainder formed the Control Group.

**This regime no longer exists.** It is the reference experiment for tick-size impact
measurement, not a description of current US market structure.

| Group | Quoting increment | Trading increment | Additional constraint |
| :--- | :--- | :--- | :--- |
| **Control** | Any permitted increment | Any permitted increment | None |
| **Test Group 1** | $0.05 — display, ranking and acceptance below $0.05 prohibited, *except* midpoint and retail-liquidity-program orders | Any increment currently permitted for NMS securities | None |
| **Test Group 2** | Same as TG1 | $0.05, subject to the exemptions below | None |
| **Test Group 3** | Same as TG1 | Same as TG2 | **Trade-at prohibition** |

**Test Group 2/3 trading exemptions** (all three apply, and omitting them is the most
common way a replication mis-measures TG2/TG3):

1. Trades at the midpoint of the NBBO or the protected best bid and offer.
2. Retail Investor Orders receiving price improvement of at least **$0.005** better than
   the best protected bid or offer.
3. Negotiated Trades.

**Trade-at (TG3)** prevents a non-quoting trading centre from price-matching a protected
quotation; a trading centre displaying a protected quotation may execute at that price only
up to its displayed size. The Plan lists thirteen exceptions, including block-size
executions, retail price improvement of at least $0.005, Intermarket Sweep Orders,
Trade-at ISOs, single-priced auction transactions, and executions during a displaying
centre's systems failure or material delay.

## 2. Current and forthcoming regimes

| Regime | Instrument scope | Increment | Status |
| :--- | :--- | :--- | :--- |
| **SEC Rule 612** (17 CFR 242.612) | NMS stocks | $0.01 at or above $1.00; $0.0001 below $1.00 | Operative |
| **Amended Rule 612** (adopted 2024-09-18) | NMS stocks priced ≥ $1.00 that the listing exchange designates tick-constrained (Time Weighted Average Quoted Spread ≤ $0.015) | **$0.005** | **Not operative.** Compliance was first set for 2025-11-03, then deferred; SEC exemptive relief of 2026-06-11 (Release 34-105656) extends it to the first business day of **November 2027**. It is a per-symbol assignment, not a function of price. |
| **MiFID II RTS 11** (Commission Delegated Regulation (EU) 2017/588) | EU shares, depositary receipts, ETFs | A cell in a 19 price range × 6 liquidity band table; the band comes from the average daily number of transactions (ADNT) published by ESMA/NCAs | Operative |

**RTS 11, not RTS 28.** RTS 28 was the annual top-five-execution-venue report under
MiFID II Article 27(6); that obligation was removed by the MiFID II/MiFIR review and ESMA
deprioritised supervision of it from 2024-02-13. It has never governed tick sizes.

## 3. Spread metric definitions (SEC Rule 605 / 17 CFR 242.600(b))

Let `D = +1` for a buy-side aggressor and `D = -1` for a sell-side aggressor, `M` the NBBO
midpoint benchmark, and `M_{t+h}` the NBBO midpoint `h` after execution.

1. **Quoted spread** — `P_ask - P_bid`. Defined at 17 CFR 242.600(b)(12) as the
   *share-weighted* average of that difference at the time of order receipt.
2. **Effective spread** — `2 x D x (P_trade - M)`. Rule 605 benchmarks `M` at the **time of
   order receipt** (17 CFR 242.600(b)(8)); microstructure research conventionally uses the
   prevailing quote at trade time, which is what `scripts/` implements. A print at the
   midpoint gives exactly zero; a print through the midpoint gives a negative value.
3. **Realized spread** — `2 x D x (P_trade - M_{t+h})` (17 CFR 242.600(b)(13)).
   *Proviso:* where the final NBBO disseminated for regular trading hours arrives less than
   `h` after execution, that final midpoint must be used.
4. **Adverse selection / price impact** — `effective - realized`. In basis points this
   module divides by the average midpoint, mirroring the Rule 605 "average percentage
   spread" construction at 17 CFR 242.600(b)(10)–(11): a **ratio of averages**, not an
   average of per-trade ratios.
5. **E/Q ratio** — average effective spread ÷ average quoted spread. Below 1 indicates
   execution inside the quote.

**Horizons.** Amended Rule 605 requires realized spread at **50 ms, 1 s, 15 s, 1 min and
5 min** (17 CFR 242.605(a)(1)(i)(O)–(X)). The 5-minute figure is the Pilot's horizon and
this module's, not a universal standard — the Pilot Assessment found that at horizons of
1 ms to 1 s at least one test group showed a *lower* realized spread than the control,
while the 5-minute and 30-minute horizons showed the largest gaps.

**Weighting.** Every Rule 605 spread average is share-weighted. Equal weighting gives an
odd lot the same influence as a block and will not reconcile with any published figure.

## 4. Measured Pilot outcomes

From the Participants' Assessment (2018-07-03), pre-pilot vs pilot period. Note the
denominators differ: quoted spreads are reported in basis points, effective spreads in
cents per share, so the two percentages are **not** directly comparable.

| Metric | Control | TG1 | TG2 | TG3 |
| :--- | ---: | ---: | ---: | ---: |
| Average quoted spread (bps) | +0.73% | +14.46% | +13.68% | +23.58% |
| Share-weighted effective spread (¢/share) | −5.45% | +59.26% | +54.08% | +53.87% |
| Share-weighted price improvement (¢/share) | −14.66% | +39.69% | +51.88% | +46.70% |
| NBBO depth, dollar terms | +18.84% | +254.90% | +270.33% | +333.49% |
| E/Q ratio | −3.80% | +19.43% | +15.15% | +10.13% |
| Shares executed (of shares ordered) | 1.2% → 1.5% | 1.1% → 1.5% | 1.1% → 1.6% | 1.1% → 2.2% |

**The effect is concentrated, not uniform.** Splitting by pre-pilot spread class, quoted
spreads for previously "very tight" securities (< $0.025) rose +180.65% / +202.71% /
+193.54% for TG1/TG2/TG3, while securities already quoting near or above $0.10 saw spreads
*narrow* by 6–17%. Depth in shares rose as much as +691.61% (TG3, very tight) and as little
as +109.77% (TG1, ≥ $0.10). Depth in shares is inflated by low-priced, high-volume
securities; the dollar-terms figures above are the cleaner comparison.

**Caveats that matter for any replication.** The headline numbers above are raw pre/post
changes. The Assessment's difference-in-differences tests found the depth and quoted-spread
increases statistically significant, but the changes in share-weighted effective spread and
in 5-minute realized spread were **not** statistically significant. Do not carry any of
these percentages into a model as a coefficient.

## 5. Sources

- Assessment of the Plan to Implement a Tick Size Pilot Program, submitted to the NMS Plan
  Participants, 2018-07-03 (prepared by Rosenblatt Securities). Group definitions pp. 6–7;
  quoted spreads Fig 19–20; effective spreads and price improvement Fig 23–24; realized
  spreads and the Rule 605 definition p. 23 n.13; E/Q ratio Fig 26; fill rates Fig 28;
  depth Fig 9, Fig 21. <https://www.sec.gov/files/TICK%20PILOT%20ASSESSMENT%20FINAL%20Aug%202.pdf>
- 17 CFR 242.600(b) — definitions of average quoted, effective and realized spread and
  average midpoint. <https://www.ecfr.gov/current/title-17/part-242/section-242.600>
- 17 CFR 242.605 — required execution-quality statistics and realized-spread horizons.
  <https://www.ecfr.gov/current/title-17/part-242/section-242.605>
- 17 CFR 242.612 — minimum pricing increment.
  <https://www.ecfr.gov/current/title-17/part-242/section-242.612>
- SEC, Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of Better
  Priced Orders, adopted 2024-09-18. <https://www.sec.gov/newsroom/press-releases/2024-137>
- SEC Release No. 34-105656 (2026-06-11), exemptive order extending compliance with
  Rules 600(b)(89)(i)(F), 610(c) and 612 to the first business day of November 2027.
  <https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf>
- Commission Delegated Regulation (EU) 2017/588 (RTS 11) — tick size regime for shares,
  depositary receipts and ETFs. <https://eur-lex.europa.eu/eli/reg_del/2017/588/oj>
- ESMA, Public Statement on the deprioritisation of supervisory actions on RTS 28
  reporting, ESMA35-335435667-5871, 2024-02-13.
  <https://www.esma.europa.eu/sites/default/files/2024-02/ESMA35-335435667-5871_Public_Statement_on_deprioritisation_of_supervisory_actions_on_RTS_28_reporting.pdf>

## 6. Algorithm recalibration matrix

The direction of each response follows from the measured deltas; **no magnitude is
prescribed**, because the Pilot's own effect sizes span an order of magnitude across
securities. Measure, then tune.

| Execution strategy | What a coarser tick does | What to re-measure and tune |
| :--- | :--- | :--- |
| **Passive market making** | Fewer price points; deeper resting queue at each; longer expected time to fill | Realised queue position and fill probability — not assumed. Pegged order types with an offset; inventory limits and cancel latency sized to the *measured* adverse-selection change |
| **TWAP / VWAP slicing** | Crossing cost rises by the change in **effective** spread, not by the tick ratio | Passive/aggressive mix and price caps derived from measured effective spread; re-fit the participation schedule |
| **Momentum taker** | Higher cost per marketable order | Signal conviction threshold required before an IOC or sweep |
| **Stat arb** | Entry/exit bands quantised to a coarser grid; cost paid on every leg | Band width against the new tick; leg-level cost assumptions; whether the signal still clears one tick |
