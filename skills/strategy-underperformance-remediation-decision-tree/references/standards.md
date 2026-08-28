# Standards for Strategy Underperformance Remediation Decision Tree

## Engine policy (repo engineering standard, not a regulatory mandate)

The node order and every threshold below are this repo's default calibration. **No
regulator prescribes a Sharpe of 1.0, a peer floor of 0.50, or a 50% slippage-to-alpha
limit**, and no external standard defines this decision tree. They are configurable
policy choices a firm calibrates to its own mandate, instrument mix, and holding
period, and a firm should be able to defend each one to its capital-allocation
committee.

| Node / Metric | Default (house policy) | Comparison | Remediation action |
|---|---|---|---|
| Node 1 — Hypothesis validity | `is_alpha_hypothesis_valid = False` | Boolean, strict `bool` type | `MANDATORY_STRATEGY_DECOMMISSION` |
| Node 2 — Data feed health | `is_data_feed_healthy = False` | Boolean, strict `bool` type | `OPTIMIZE_EXECUTION_AND_DATA` |
| Node 2 — Slippage-to-alpha ratio | $> 50.0\%$ | Strict `>` — exactly 50% clears | `OPTIMIZE_EXECUTION_AND_DATA` |
| Node 2a — Live observations | `< min_live_observations` (opt-in, default off) | Strict `<` | `EXTEND_OBSERVATION_INSUFFICIENT_HISTORY` |
| Node 3 — Joint impairment | Live $< 1.0$ **and** peer $< 0.50$ | Strict `<` on both | `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL` |
| Node 4 — Idiosyncratic drift | Live $< 1.0$ **and** peer $\ge 0.50$ | Inclusive `>=` on the peer leg | `RECALIBRATE_MODEL_PARAMETERS` |
| Healthy | Live $\ge 1.0$ | Inclusive | `MAINTAIN_TRADING` |
| Node ordering | 1 → 2 → 2a → 3 → 4 → healthy, every branch terminal | — | The order **is** the policy |
| Threshold relationship | `min_peer_sharpe <= min_healthy_sharpe`, enforced at construction | — | Otherwise Node 4 can fire for "underperforming healthy peers" against a benchmark bar stricter than the strategy's own mandate |
| Input domain | Non-finite values, negative slippage, non-positive expected alpha, non-`bool` flags, and empty `strategy_id` MUST be rejected, never compared | — | Every node is a threshold test, and every comparison against `NaN` is `False`, so a corrupt payload otherwise returns `MAINTAIN_TRADING` |

### Why the asymmetric Sharpe thresholds matter

The mandate floor (1.0) and the peer health floor (0.50) are different numbers, so the
Node 3 / Node 4 split is *not* a relative-performance test. A strategy at 0.95 against
peers at 0.55 is routed to Node 4 while leading its cohort. That is a deliberate
consequence of measuring absolute mandate compliance and peer health separately, and
the engine reports `sharpe_gap_vs_peer` and warns when the gap is non-negative so the
reader can see it. Setting `min_peer_sharpe = min_healthy_sharpe` collapses the two
into a genuine relative comparison, at the cost of routing far more strategies to
Node 3.

## Statistical basis and limits of the Sharpe thresholds

Lo, Andrew W. (2002), "The Statistics of Sharpe Ratios", *Financial Analysts Journal*
58(4), pp. 36-52. Under IID returns the estimated Sharpe ratio has asymptotic standard
error $\text{SE}(\widehat{\text{SR}}) = \sqrt{(1 + \text{SR}^2/2)/T}$; Eq. 17-18 give
the IID time-aggregation rule $\text{SR}(q) = \sqrt{q}\,\text{SR}$. Composing them:

$$\text{SE}(\text{SR}_{\text{ann}}) = \sqrt{\frac{q + \text{SR}_{\text{ann}}^2/2}{T}}$$

| $T$ (daily obs) | Horizon | $\text{SE}(\text{SR}_{\text{ann}})$ at SR $= 1.0$ |
|---|---|---|
| 60 | ~3 months | $\approx 2.05$ |
| 252 | 1 year | $\approx 1.00$ |
| 1,008 | 4 years | $\approx 0.50$ |
| 2,520 | 10 years | $\approx 0.32$ |

At every horizon a governance committee actually works with, the standard error is
comparable to or larger than the 1.0 threshold being tested. Nodes 3 and 4 are
therefore **policy floors that exclude visibly broken strategies**, not statistical
findings, and the report's `sharpe_evidence_conclusive` flag — true only when
`live_sharpe` sits more than 1.96 standard errors from the mandate threshold — will
normally be `False`. Verified against Lo's published Table 1 values ($T = 60$:
SE $= 0.188$ at SR $= 1.50$, SE $= 0.303$ at SR $= 3.00$), reproduced as unit tests.

Two caveats on the formula itself. It is an **IID** result: under serial correlation —
routine in strategy returns, severe for smoothed or illiquid marks — it *understates*
the true standard error, so treat it as a floor on estimation error. And the
$\sqrt{q}$ annualization it assumes is itself invalid under serial correlation, which
affects the reported Sharpe ratios and the threshold comparison alike.

The engine deliberately does **not** attempt a significance test of the
strategy-versus-peer difference. That requires a statistic with a defined null
distribution — the Jobson-Korkie statistic with the Memmel (2003) correction — and
lives in `strategy-performance-decay-detection-vs-market-wide-decay`.

## Regulatory touchpoints

Jurisdiction-specific. **None of the sources below mandates this decision tree, its
node order, or any of its thresholds.** They establish that acting on a remediation
recommendation — recalibrating parameters, changing execution behaviour, replacing a
data feed, retiring a strategy — is a governed, recorded, retested event rather than a
researcher's unilateral edit.

| Source | Jurisdiction | Status | Relevance |
|---|---|---|---|
| Commission Delegated Regulation (EU) 2017/589 (**RTS 6**), Art. 5 (testing and deployment) | EU; assimilated law in the UK via the FCA Handbook | Binding | "A person designated by the senior management of the investment firm shall authorise the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy." A recalibration or a decommissioning is such an update, so this engine's output is a **recommendation for authorisation**, never an auto-executing change. Art. 5 also requires clearly delineated development and testing methodologies **prior to** deployment or substantial update — the recalibrated parameter set must be retested before it goes back live. |
| RTS 6 Art. 5(7) | EU / UK | Binding | Records of "any material change made to the software used for algorithmic trading", sufficient to determine when the change was made, who made it, who approved it, and its nature. The remediation report — action, decisive node, cleared nodes, thresholds in force, caveats — is the *input* to that record, not the record itself. |
| RTS 6 Art. 9 (annual self-assessment and validation) | EU / UK | Binding | Annual self-assessment and validation of algorithmic trading systems, the governance and approval framework, and overall compliance with Art. 17 of Directive 2014/65/EU. A documented remediation triage history is evidence for that assessment. |
| ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 February 2026 | EU | **Non-binding** — issued under Art. 29(2) of the ESMA Regulation as a convergence tool, explicitly "not subject to a 'comply or explain' mechanism" (¶3) | ¶31: "A material change or substantial update is any modification that may alter the behaviour, risk profile, or compliance posture of an algorithm, algorithmic trading system or algorithmic trading strategy. Investment firms are required to timestamp, approve, and record all material changes." Its non-exhaustive retesting list covers **Logic or Decision Rules** ("altering how the algorithm determines price, timing, or quantity" — i.e. `RECALIBRATE_MODEL_PARAMETERS`), **Execution Behaviour** ("modifying order types, slicing logic, or routing mechanisms") and **External Dependencies** ("replacing third-party providers or data feeds") — the two limbs of `OPTIMIZE_EXECUTION_AND_DATA`. ¶22: firms "are required to test and validate each algorithmic trading strategy before deployment and after any material change". |
| ESMA supervisory briefing ¶30 | EU | Non-binding | Directly on point for this engine: firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested." A triage engine emitting `RECALIBRATE_MODEL_PARAMETERS` on a governance cadence is precisely that mechanism. Record and retest each recalibration individually. |

**Not applicable here, despite superficial fit.** SEC Rule 15c3-5 governs pre-order-entry
risk controls under a broker-dealer's direct and exclusive control; nothing in this
engine touches order entry, so citing it for a periodic governance diagnostic would be
a misapplication.

Sources:
[Lo (2002), FAJ 58(4)](https://www.tandfonline.com/doi/abs/10.2469/faj.v58.n4.2453) ·
[RTS 6 (EU) 2017/589](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589) ·
[RTS 6, FCA Handbook (assimilated)](https://www.handbook.fca.org.uk/techstandards/MIFID-MIFIR/2017/reg_del_2017_589_oj/chapter-ii/?view=chapter) ·
[ESMA Supervisory Briefing on Algorithmic Trading in the EU](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf)

The Jobson-Korkie/Memmel Sharpe-difference statistic referenced above is documented,
derived, and tested in `strategy-performance-decay-detection-vs-market-wide-decay`;
this skill does not implement it.

> **Verification note.** The RTS 6 Art. 5 sentence quoted above is reproduced verbatim
> from the regulation, but the sub-paragraph number could not be confirmed against a
> retrievable primary text at the time of writing (Art. 5(4) is the (a)-(d) criteria
> list), so it is cited to the article rather than the paragraph. The ESMA quotations
> at ¶22, ¶30, ¶31 and ¶3 were extracted directly from the published PDF.
