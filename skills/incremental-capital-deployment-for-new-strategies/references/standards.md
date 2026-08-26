# Standards for Incremental Capital Deployment

## Engine policy (repo engineering standard, not a regulatory mandate)

The tier ladder and its thresholds are this repo's default calibration. They are
**not** prescribed by any regulator; every number below is a configurable policy
choice a firm should calibrate to its own risk appetite and instrument mix.

| Metric | Engineering Standard |
|---|---|
| Tier Allocation Percentages | Tier 0 = 0%, Tier 1 = 10%, Tier 2 = 50%, Tier 3 = 100%. |
| Tier 0 -> 1 Promotion Gate | $\ge 14$ paper days, Max DD $\le 5.0\%$, and **0 execution crashes**. First commitment of real capital, so it is screened rather than time-only. |
| Tier 1 -> 2 Promotion Gate | $\ge 30$ live days, Sharpe $\ge 1.0$, Max DD $\le 5.0\%$, Slippage $\le 1.5\times$. |
| Tier 2 -> 3 Promotion Gate | $\ge 60$ live days, Sharpe $\ge 1.2$, Max DD $\le 8.0\%$, Slippage $\le 1.5\times$. The largest single capital step in the ladder carries a slippage gate at least as strict as the step below it. |
| Maintenance Limits (one-tier step-down) | Max DD $> 8.0\%$ (Tier 1) or $> 10.0\%$ (Tiers 2-3), or Slippage $> 2.0\times$ at any tier. Deliberately looser than the entry gate above them, giving a hysteresis band. |
| Sharpe and demotion | Sharpe MUST NOT trigger demotion. Only realized drawdown and realized slippage de-risk (see the statistical basis below). |
| Emergency Kill Switch Limit | Max Drawdown $\ge 12.0\%$ MUST trigger immediate demotion to Tier 0 ($0\%$ allocation), outranking every other condition. Maintenance limits MUST be configured strictly below it, or the step-down is unreachable. |
| Movement per evaluation | At most one tier up or one tier down, except the emergency branch, which goes directly to Tier 0. |
| Input domain | Non-finite (`NaN`/`Inf`) and negative-signed drawdowns MUST be rejected, never compared. Every gate is a threshold test and every comparison against `NaN` is False, so a `NaN` drawdown silently bypasses the emergency gate. |

## Statistical basis for the Sharpe gates

Lo, Andrew W. (2002), "The Statistics of Sharpe Ratios", *Financial Analysts Journal*
58(4), pp. 36-52. Under IID returns the estimated Sharpe ratio has asymptotic standard
error $\text{SE}(\widehat{\text{SR}}) = \sqrt{(1 + \text{SR}^2/2)/T}$; Eq. 17-18 give the
IID time-aggregation rule $\text{SR}(q) = \sqrt{q}\,\text{SR}$. Composing them:

$$\text{SE}(\text{SR}_{\text{ann}}) = \sqrt{\frac{q + \text{SR}_{\text{ann}}^2/2}{T}}$$

| Gate | $T$ (daily obs) | Threshold | $\text{SE}(\text{SR}_{\text{ann}})$ |
|---|---|---|---|
| Tier 1 -> 2 | 30 | 1.0 | $\approx 2.90$ |
| Tier 2 -> 3 | 60 | 1.2 | $\approx 2.05$ |
| — | 1,010 | 1.0 | $0.50$ |

The standard error at both promotion gates exceeds the threshold being tested, so
neither gate is statistically conclusive. Reaching $\pm 1.0$ precision at 95%
confidence on a Sharpe of 1.0 requires ~1,010 daily observations — about four years.
The gates remain useful as **floors** that exclude visibly broken strategies; they are
not evidence of edge, and `sharpe_gate_conclusive` in the report reports which of the
two a given evaluation is. Verified against Lo's published Table 1 values
($T=60$: SE $=0.188$ at SR $=1.50$, SE $=0.303$ at SR $=3.00$), reproduced as unit tests.

Max drawdown carries the mirror-image bias: for a driftless process the expected
running maximum drawdown grows roughly with $\sqrt{\text{window}}$, so a fixed
emergency limit applied to a 30-day Tier 1 window is more permissive than the same
limit applied to a 60-day Tier 2 window on the identical strategy.

## Regulatory touchpoints

Jurisdiction-specific. None of the following mandates this particular 4-tier ladder or
any of its numeric thresholds; they establish that *controlled, authorised, limited*
deployment and pre-set capital thresholds are required, and the ladder is one way to
implement that.

| Source | Jurisdiction | Status | Relevance |
|---|---|---|---|
| Commission Delegated Regulation (EU) 2017/589 (**RTS 6**), Art. 8 "Controlled deployment of algorithms" (anchored on MiFID II Art. 17(1)) | EU; assimilated law in the UK via the FCA Handbook | Binding | Requires controlled deployment of a trading algorithm into a live environment. The recitals describe setting cautious limits on the instruments traded, the price, value and number of orders, the strategy positions and the number of markets involved, with more intensive monitoring during that period. This skill's Tier 1/Tier 2 partial allocations are a capital-dimension implementation of that. |
| RTS 6 Art. 5 "General methodology" | EU / UK | Binding | Deployment or substantial update of an algorithmic trading system, algorithm or strategy must be authorised by **a person designated by senior management**. A tier promotion changes strategy exposure, so the engine's output is a recommendation for authorisation, not an auto-executing capital change. |
| RTS 6 Art. 12 "Kill functionality" | EU / UK | Binding | Separate from this skill. `EMERGENCY_DEACTIVATED` sets a capital entitlement to $0; it does not cancel orders or flatten positions. See `kill-switch-and-drawdown-circuit-breakers`. |
| ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 February 2026 | EU | **Non-binding** (issued under Art. 29(2) ESMA Regulation; explicitly not subject to comply-or-explain, ¶3) | ¶31: a material change is any modification that may alter the behaviour, risk profile or compliance posture of an algorithm or strategy, and firms are required to timestamp, approve and record all material changes. ¶30 warns that a series of small recalibrations can accumulate into an untested material change — directly applicable to a sequence of tier promotions, each of which must be individually recorded. |
| SEC Rule 15c3-5(c)(1)(i) (Market Access Rule) | US broker-dealers | Binding | Risk management controls must be reasonably designed to prevent entry of orders exceeding appropriate **pre-set credit or capital thresholds**. In SEC usage "credit" refers to customer activity and "capital" to the firm's own account, so a proprietary strategy's tier allocation is a capital threshold. Rule 15c3-5(b) additionally requires those controls be under the broker-dealer's direct and exclusive control. This engine *produces* the threshold; a pre-trade control must *enforce* it. |

Sources: [Lo (2002), FAJ 58(4)](https://www.tandfonline.com/doi/abs/10.2469/faj.v58.n4.2453) ·
[RTS 6 (EU) 2017/589](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32017R0589) ·
[RTS 6 Art. 8, FCA Handbook](https://www.handbook.fca.org.uk/techstandards/MIFID-MIFIR/2017/reg_del_2017_589_oj/chapter-ii/section-i/012.html) ·
[ESMA Supervisory Briefing on Algorithmic Trading](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf) ·
[17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5)
