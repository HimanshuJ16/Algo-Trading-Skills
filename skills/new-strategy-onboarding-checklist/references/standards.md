# Standards for New Strategy Onboarding Checklist

## The thresholds in this skill are house defaults, not standards

The numbers below are not standards, and nothing sources them. No regulator, exchange
or published body of research mandates fourteen days of paper trading, three market
regimes, a walk-forward score of $0.70$, or a backtest Sharpe of $1.5$. They are
starting values chosen for this engine.

| Threshold | Default | What it is |
|---|---|---|
| `min_walk_forward_score` | $0.70$ | House heuristic. The engine imposes **no scale** on this metric — the number and the threshold must come from the same convention. |
| `min_regimes_covered` | $3$ | House heuristic. Pairs with `multi-year-regime-coverage-requirement`, which counts regimes; that skill's own defaults are equally heuristic. |
| `min_backtest_sharpe` | $1.5$ | House heuristic, and the most dangerous of the four — see "Selecting on backtest Sharpe" below. |
| `min_paper_trading_days` | $14$ | House heuristic. Calendar vs trading days is the caller's convention; 14 calendar days $\approx$ 10 trading days. |
| `max_paper_trading_errors` | $0$ | The only defensible default for a control-integrity gate: a critical execution error during the paper run is a finding, not a statistic. |

Calibrate these per asset class and holding period, and record what you used. The
engine embeds `policy_applied` in every report for exactly this reason — a stored
verdict without its policy snapshot proves nothing, because a config of zeros emits
the same `ONBOARDING_PASSED` string as the strict default.

## Selecting on backtest Sharpe

Raising `min_backtest_sharpe` tightens a screen on an **in-sample** statistic. The
maximum Sharpe over many research trials exceeds the mean by construction, and the
overstatement grows with the number of trials, so a higher floor preferentially admits
the most heavily over-fitted candidate in the pipeline. Treat the floor as a screen
against visibly broken strategies, never as evidence of edge, and correct for trial
count downstream.

The established treatments (verified bibliographically; full texts were not retrievable
through an open URL during this review, so no formula is reproduced here — apply them
from the papers themselves):

- Bailey, D. H. and Lopez de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting
  for Selection Bias, Backtest Overfitting and Non-Normality" —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Bailey, D. H., Borwein, J., Lopez de Prado, M. and Zhu, Q. J., "The Probability of
  Backtest Overfitting", *Journal of Computational Finance*, DOI 10.21314/JCF.2016.322 —
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- In-repo: `factor-research-multiple-testing-correction` (Bonferroni / Holm /
  Benjamini-Hochberg FDR and the Harvey-Liu-Zhu $t \ge 3.0$ hurdle).

## Regulatory context

The gates in this skill are modelled on obligations that exist; the *numbers* are not.
Nothing below makes a firm "compliant" by running this engine.

| Instrument | Jurisdiction & scope | Status | What it actually says | Relevance |
|---|---|---|---|---|
| **MiFID II RTS 6** — Commission Delegated Regulation (EU) 2017/589, **Art. 5** "General methodology" | EU investment firms engaged in algorithmic trading; assimilated into UK law via the FCA Handbook | Binding | Art. 5(1): "Prior to the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy, an investment firm shall establish clearly delineated methodologies to develop and test such systems, algorithms or strategies." Art. 5(2): "A person designated by the senior management of the investment firm shall authorise the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy." Art. 5(4) requires the methodology to establish that the algorithm "(a) does not behave in an unintended manner; … (d) does not contribute to disorderly trading conditions, continues to work effectively in stressed market conditions and, where necessary under those conditions, allows for the switching off of the algorithmic trading system or trading algorithm." | The legal anchor for the whole skill. **Art. 5(2) is why a clean report is not an authorisation**: the engine's output is an input to a designated person's decision. Art. 5(4)(d) is why the kill-switch flag sits inside the operational gate. Note what Art. 5 does *not* contain: any testing duration, any performance metric. |
| **RTS 6 Art. 7** "Testing environments" | As above | Binding | Art. 7(1) requires testing of the Art. 5(4)(a), (b) and (d) criteria "in an environment that is separated from its production environment". Art. 7(2)-(3): the firm may use its own environment or one provided by a trading venue, DEA provider or vendor, but "shall retain full responsibility for the testing". | Supports the paper-trading gate in principle. It does not set a duration, and outsourcing the environment does not outsource responsibility for the attestation. |
| **RTS 6 Art. 8** "Controlled deployment of algorithms" | As above | Binding | "Before deployment of a trading algorithm, an investment firm shall set predefined limits on: (a) the number of financial instruments being traded; (b) the price, value and numbers of orders; (c) the strategy positions; and (d) the number of trading venues to which orders are sent." | **Separate from this skill.** `ONBOARDING_PASSED` sets none of these limits. See `incremental-capital-deployment-for-new-strategies` and `correlation-aware-exposure-limits`. |
| **RTS 6 Art. 9** "Annual self-assessment and validation" | As above | Binding | "An investment firm shall annually perform a self-assessment and validation process and on the basis of that process issue a validation report." | An onboarding pass is a point-in-time snapshot with no expiry. The annual cycle is a separate obligation this engine does not track. |
| **RTS 6 Art. 12** "Kill functionality" | As above | Binding | "An investment firm shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues." | What `kill_switch_integrated=True` is asserting the existence of. The engine records the assertion; it does not test the switch. See `kill-switch-and-drawdown-circuit-breakers`. |
| **SEC Rule 15c3-5** (17 CFR 240.15c3-5) | US **broker-dealers with market access**, or providing access via their MPID. Not a general obligation on funds or prop traders without market access | Binding rule | (b): such a broker-dealer "shall establish, document, and maintain a system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks of this business activity." (c)(1)(i): controls must "[p]revent the entry of orders that exceed appropriate pre-set credit or capital thresholds …". | The pre-trade boundary. This onboarding gate is a governance record, not a control that rejects orders. |
| **SR 26-2** / OCC Bulletin **2026-13**, *Revised Guidance on Model Risk Management* (Federal Reserve, OCC, FDIC, 17 April 2026) | US banking organizations; "expected to be most relevant to banking organizations with over \$30 billion in total assets" | Supervisory **guidance**, not an enforceable rule | Frames model development, validation, monitoring and governance across the lifecycle. | The reference framework behind Gate 3. **Applicability caveat**: a hedge fund, prop firm or asset manager is not a banking organization and cannot be "compliant" or "non-compliant" with it. **SR 11-7 (4 April 2011) was superseded by SR 26-2 on 17 April 2026 and must not be cited as current guidance.** |

## What no instrument in the table requires

- A minimum paper-trading or testing **duration**.
- A minimum **Sharpe ratio**, walk-forward score, or any other performance metric.
- A minimum count of **market regimes**.

Those four numbers are yours to set and yours to defend.

## Sources

- [Commission Delegated Regulation (EU) 2017/589 (RTS 6)](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589) — Arts. 5, 7, 8, 9, 12
- [17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5) — paras (b), (c)(1)(i)
- [Federal Reserve SR 26-2](https://www.federalreserve.gov/supervisionreg/srletters/sr2602.htm) — 17 April 2026, superseding SR 11-7 and SR 21-8
