# Standards — options-pin-risk-management-at-expiry

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator,
exchange or clearing house publishes a mandatory "pin band" or a mandatory
pre-close flattening window. The distance that matters is how far the underlying
can plausibly travel between the close and the 5:30 p.m. ET exercise-decision
deadline — a volatility question, not a fixed percentage. Calibrate against the
underlying's after-hours behaviour and record the rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `pin_distance_pct` | $1.0\%$ | Pin band as a percentage of spot. Scales the wrong way at both ends: 1% of a \$5 underlying is \$0.05, 1% of a \$600 underlying is \$6.00. |
| `pin_distance_abs_usd` | `None` | Optional absolute band per share, applied with OR alongside the percentage band. Disabled by default so the module invents no threshold. |
| `pin_cutoff_hours` | $2.0$ | Hours before **trading close** (not expiry) at which pin risk becomes actionable. |
| `ex_by_ex_threshold_usd` | \$0.01 | The one non-tunable value here in practice — it is OCC's, not the desk's. Exposed only so a non-US clearing house's threshold can be substituted. |
| `contract_multiplier` | $100$ | Per position, not per engine. 100 is the standard US equity option unit; adjusted and mini contracts are not. |

## Market-structure facts (verified against primary sources)

| Fact used | Source | Applicability |
|---|---|---|
| An expiring standardized equity option in the money by **\$0.01 or more** per share at the close is exercised automatically under OCC's exercise-by-exception procedure unless contrary instructions are given. The threshold is an administrative convenience for clearing members, is not a direction to members about which customer positions should be exercised, and an individual firm may apply a different threshold. | OCC Rule 805; OCC investor education, "Options Exercise" — <https://www.optionseducation.org/referencelibrary/faq/options-exercise> | US exchange-listed equity options. |
| "Option holders who hold expiring options have until **5:30 p.m. Eastern Time (ET)** on the day of expiration to make a final exercise decision." Members may establish an earlier deadline for their customers but **may not accept instructions after 5:30 p.m. ET**. | FINRA Rule 2360(b)(23)(A); FINRA Information Notice, 3 Feb 2021, "Exercise Cut-Off Time for Expiring Options" — <https://www.finra.org/rules-guidance/notices/information-notice-020321>, <https://www.finra.org/rules-guidance/rulebooks/finra-rules/2360> | **Expiring** options. This is a ceiling, not the deadline a given customer faces. |
| A Contrary Exercise Advice conveys a holder's final decision **either** not to exercise an option that would otherwise be automatically exercised under OCC Rule 805, **or** to exercise one that would otherwise not be exercised. | FINRA Rule 2360(b)(23)(A); OCC investor education (above) | This bidirectionality is why an out-of-the-money short is not safe and an in-the-money short is not certain to be assigned. |
| The options exchanges' cut-off for receiving an exercise notice is **4:30 p.m. CT**. | OCC investor education, "Options Exercise" (above) | 4:30 p.m. CT **is** 5:30 p.m. ET — the same deadline, not an earlier one. See the correction note below. |
| Assignment notices are allocated by the member on a FIFO basis, by random selection, or by another FINRA-approved equally-random method, disclosed to customers in writing. | FINRA Rule 2360(b)(23)(C) — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/2360> | Why this skill emits a directive and never a probability of assignment. |
| Standard equity option contracts cover **100 shares**; regular trading hours are 8:30 a.m. – 3:00 p.m. CT (9:30 a.m. – 4:00 p.m. ET). | Cboe, Equity Options Specifications — <https://www.cboe.com/exchange-traded-stock/equity-options-spec/> | US listed equity options. Index products differ: Cboe Mini-SPX (XSP) is one-tenth of SPX. |
| The contract's **expiration time is 11:59 p.m. ET on the expiration date**; since contracts expiring on or after 1 Feb 2015, the expiration date is the third Friday rather than the following Saturday. | OCC By-Laws, as described in SEC Release 34-69772 approving SR-OCC-2013-04 — <https://www.federalregister.gov/documents/2013/06/21/2013-14793/self-regulatory-organizations-the-options-clearing-corporation-order-approving-proposed-rule-change> | Establishes that expiration time is **not** the operative deadline for any action a position holder can take. |
| OCC publishes an adjustment memo when a corporate action changes a contract's terms; the deliverable becomes whatever a 100-share position became. A reverse split or odd split leaves a deliverable that is not 100 shares, and the contract may deliver cash in lieu, another security, or a basket. | OCC investor education, "Splits, Mergers, Spinoffs & Bankruptcies" — <https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies> | Why `contract_multiplier` is a per-position input rather than a constant. |
| Cash-settled index options settle in cash at an exercise-settlement value. AM-settled monthlies (SPX, NDX, RUT) cease trading on the business day *preceding* the expiration date; PM-settled weeklies (SPXW) cease at 4:00 p.m. ET on the day of expiration. | Cboe, S&P 500 Index Options Product Specifications — <https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/> | Why cash-settled positions are reported with a zero share delta and referred elsewhere. |

### Correction note — the "4:30 p.m." figure

Earlier revisions of this skill recorded the contrary-exercise deadline as
"4:30 p.m. EST". That conflated two things. **4:30 p.m. CT is the exchanges'
cut-off, and it is the same instant as the 5:30 p.m. ET deadline in FINRA Rule
2360(b)(23)(A)** — relabelling it "ET" invents a deadline an hour earlier than
any published one. "EST" was wrong independently: the third Friday falls in
Eastern *Daylight* Time from March to November, so an EST label is off by an
hour for most expirations. Brokers do commonly impose earlier cutoffs, but the
correct instruction is to look up your own broker's published time, not to
assume a number.

## Engineering standards enforced by this skill

| Metric | Engineering Standard |
|---|---|
| Input integrity | Non-finite prices, non-finite hours, a zero position quantity, a non-positive strike or multiplier, and an unrecognised `option_type` or `settlement_type` MUST raise. A risk control MUST NOT return "safe" on data it could not evaluate. |
| Moneyness | The exercise-by-exception test MUST be applied to signed moneyness ($S-K$ for calls, $K-S$ for puts) on a value rounded to the cent scale before comparison, so an underlying that closed exactly \$0.01 in the money is not misclassified by binary floating point. |
| Asymmetry | An out-of-the-money short MUST NOT be reported as safe on the ground that exercise-by-exception would abandon it, and an in-the-money short MUST NOT be reported as certain to be assigned. |
| Exposure reporting | Share notional (at spot) and assignment cash (at strike) MUST be reported separately. Funding requirements MUST be sized at the strike. |
| Settlement | Cash-settled contracts MUST be reported with a zero share delta and MUST NOT be assigned a share notional. |
| Timing | The cutoff MUST be measured against the close of trading in the option, not against the contract's expiration time. Once trading has closed the engine MUST NOT emit a close directive it knows cannot be executed. |
| Spread integrity | A long leg MUST NOT be counted as covering a pinned short unless it is itself in the money beyond the exercise-by-exception threshold and outside the pin band. |
| Output | The engine MUST NOT publish a probability of assignment. |

## Limitations — not modelled here

- **No probability model.** Allocation from OCC to the clearing member and then
  to an individual account is not an input.
- **No session, holiday or broker-cutoff calendar.** `hours_to_trading_close` is
  supplied by the caller.
- **No after-hours volatility model.** The pin band is a distance threshold, not
  a probability that the underlying crosses the strike before 5:30 p.m. ET.
- **No settlement-value model for cash-settled contracts.** The AM/PM
  distinction, and the risk that an AM-settled contract settles off the next
  morning's opening prints, are out of scope.
- **US market structure only.** OCC Rule 805, the FINRA cut-off and the
  contrary-exercise mechanism are US constructs. Thresholds, deadlines and
  automatic-exercise conventions elsewhere are set by the local clearing house
  and are not covered by the sources above.
