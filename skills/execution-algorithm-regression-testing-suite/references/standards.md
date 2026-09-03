# Standards — execution-algorithm-regression-testing-suite

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry or regulatory standards. No regulator
publishes a maximum tolerable shortfall degradation, a minimum fill-rate ratio, or a
maximum participation rate for algorithm release testing. ESMA was asked to prescribe the
tests firms should run and expressly declined (ESMA70-156-4572, ¶187–188). Calibrate each
threshold against your own venues, instruments, order sizes and mandate, and record the
rationale alongside the release.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_allowed_is_degradation_bps` | $+2.0$ bps | Rejects a scenario whose candidate Implementation Shortfall exceeds the baseline by more than this. Compared on exact values; display rounding never decides a release. |
| `min_allowed_fill_ratio` | $0.98$ | Rejects a scenario where $\text{FillRate}_{\text{cand}} / \text{FillRate}_{\text{base}}$ falls below this. Catches the candidate that "improves" slippage by abandoning part of each order. |
| `max_allowed_participation_rate` | $0.20$ | Absolute ceiling on peak share of market volume consumed. A liquidity-consumption bound, not an ADV limit derived from any rule. |
| `required_scenario_names` | `NORMAL_VOLATILITY`, `VOLATILITY_SHOCK`, `LIQUIDITY_CRUNCH` | A suite missing any of these is rejected regardless of the results it did produce. This taxonomy is a convention of this library; substitute your own. |

Why a participation ceiling rather than a price-impact ceiling: the FIX Trading Community
recommends setting pass/fail thresholds on an algorithm's own activity — liquidity
consumption, order cancellation, messaging rate — and recommends **against** passing or
failing an algorithm on the price movement it causes, because impact is highly variable
and "often produces non-repeatable results"
([Regulatory Testing of Algorithms, Sep 2025, §2.1.2](https://www.fixtrading.org/wp-content/uploads/download-manager-files/Regulatory-Testing-of-Algorithms-White-Paper-Sep-2025.pdf)).
Implementation Shortfall is a benchmark cost measure and is used here to detect *relative
regression against a baseline replay*, not to certify market impact.

## Regulatory touchpoints (verified against primary sources)

### MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589
Organisational requirements of investment firms engaged in algorithmic trading.
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng))

| Article | Title | Relevance to this skill |
|---|---|---|
| Art. 5 | General methodology | 5(1): clearly delineated development and testing methodologies **prior to deployment or substantial update**. 5(4)(d): the algorithm does not contribute to disorderly trading conditions and continues to work effectively in stressed market conditions. 5(5): further testing after substantial changes to the system or to venue access. |
| Art. 6 | Conformance testing | Venue/DEA-provider conformance — **out of scope** for this module. |
| Art. 7 | Testing environments | 7(1): testing of the Art. 5(4)(a),(b),(d) criteria in an environment separated from production. 7(3): the firm retains full responsibility even when using a venue's or vendor's environment. |
| Art. 8 | Controlled deployment | Staged rollout after the gate — see `canary-releases-for-strategy-code-changes`. |
| Art. 9 | Annual self-assessment and validation | Annual, not per-build. |
| Art. 10 | Stress testing | Part of the Art. 9 self-assessment: high messaging-volume and high trade-volume tests at the highest levels of the previous six months **multiplied by two**, run without affecting production. This module does not perform these tests. |
| Art. 14(3) | Shutdown | The algorithm can be shut down per business continuity arrangements without creating disorderly trading conditions — see `execution-algorithm-kill-switch-integration`. |

### MiFID II RTS 7 — Commission Delegated Regulation (EU) 2017/584
Organisational requirements of trading venues. **Art. 10** requires venues to make members
certify that deployed algorithms have been tested to avoid contributing to or creating
disorderly trading conditions prior to deployment or substantial update, **and explain the
means used for that testing** — which is why the audit report is the deliverable here, not
the boolean. *Currency caveat:* RTS 7 is being recast as RTS 7a (ESMA final report
ESMA74-2134169708-7780, 10 April 2025); confirm which instrument and article numbering
apply in your jurisdiction before citing this in a filing.

### United States — what does *not* apply
- **SEC Rule 15c3-5** (17 CFR 240.15c3-5) requires a broker-dealer with market access to
  establish risk management controls and supervisory procedures, and under §(e) to review
  their effectiveness at least annually with an annual CEO certification. The rule text
  contains **no** requirement to test or regression-test trading algorithms before
  deployment. Do not cite a passing gate as 15c3-5 evidence.
  ([17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5))
- **FINRA Regulatory Notice 15-09**, *Guidance on Effective Supervision and Control
  Practices for Firms Engaging in Algorithmic Trading Strategies* (26 March 2015), is
  **guidance, not rule text**. It describes effective practices including "conducting
  testing to confirm that core code components operate as intended and do not produce
  unintended consequences", a tracked development and change-management process, and
  quality assurance performed independently of code development.
  ([finra.org/rules-guidance/notices/15-09](https://www.finra.org/rules-guidance/notices/15-09))
- **Regulation SCI** (17 CFR 242.1001) applies to SCI entities — SROs, certain ATSs, plan
  processors, clearing agencies — not to broker-dealers generally. Rule 1001(a)(2)(ii)
  requires periodic capacity stress testing of SCI systems.

### UK — supervisory expectations
- **FCA, _Algorithmic Trading Compliance in Wholesale Markets_ (February 2018), clause
  6.12.** Good practice: dynamic testing environments that assess not only how a strategy
  performs during market disruption but whether it *further contributes* to that
  disruption in combination with other trading activity. Poor practice: "Firms who conduct
  basic testing of their algorithmic trading strategies which only assess operational
  efficiency and focus on considerations such as their performance against certain
  benchmarks or the profit and loss of the strategy." A benchmark-regression gate used as
  the whole of a testing programme is the named poor practice.
  ([fca.org.uk](https://www.fca.org.uk/publication/multi-firm-reviews/algorithmic-trading-compliance-wholesale-markets.pdf))
- **FCA, _Multi-firm review of algorithmic trading controls: high-level observations_
  (21 August 2025).** Firms must maintain testing processes that identify issues before
  deployment and confirm the algorithm behaves as intended, does not contribute to
  disorderly trading, and behaves effectively under stressed market conditions. The review
  found some firms' simulation testing "lacked sophistication or did not appear to consider
  a wide range of market scenarios".
  ([fca.org.uk](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-controls-high-level-observations))

### Market abuse context for the participation ceiling
EU **MAR** (Regulation 596/2014) Annex I(a) lists, as a non-exhaustive indicator of
manipulation, the extent to which orders or transactions represent "a significant
proportion of the daily volume" in the instrument, particularly where they lead to
significant price changes. MAR prescribes **no** numeric participation threshold; the
$0.20$ default here is an engineering choice, not a legal line.

## Metric definition

Implementation Shortfall is the difference between the value of a paper portfolio traded at
the decision price and the realised value of the executed portfolio — André F. Perold,
"The Implementation Shortfall: Paper Versus Reality", *Journal of Portfolio Management*
14(3), Spring 1988, pp. 4–9 ([doi:10.3905/jpm.1988.409150](https://doi.org/10.3905/jpm.1988.409150)).
This module consumes an already-computed per-scenario IS in basis points and never
computes it; the decomposition lives in `execution-slippage-attribution-timing-vs-sizing`.

## Known limitations

- **Relative, not absolute.** The gate detects regression against a baseline. A baseline
  that is itself poor produces a passing candidate that is also poor.
- **Blind to disorderly trading.** No order-book interaction, cancellation rate, messaging
  rate or cross-algorithm interaction is measured. RTS 6 Art. 5(4)(d) is not addressed.
- **Blind to capacity.** RTS 6 Art. 10 magnitudes are not exercised.
- **Only as representative as the replayed scenarios.** Coverage enforcement checks that a
  scenario *kind* was present, not that the data behind it was actually stressed.
