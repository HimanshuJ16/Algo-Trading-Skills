# Standards for Risk Limit Breach Escalation Matrix

## 1. What is actually mandatory

These are verified obligations with a named instrument and article. Each applies
only in its own jurisdiction and to its own class of firm.

| Source | Provision | What it requires | Bearing on this skill |
|---|---|---|---|
| **MiFID II RTS 6** — Commission Delegated Regulation (EU) 2017/589 | **Art. 17**, post-trade controls | Where a post-trade control is triggered, the firm "shall undertake appropriate action, which may include adjusting or shutting down the relevant trading algorithm or trading system or an orderly withdrawal from the market". | The legal basis for a *graduated* response. The regulation names adjust / shut down / withdraw — it does not prescribe tiers, ratios or timings. |
| **MiFID II RTS 6** | **Art. 16**, real-time monitoring | Monitoring in real time during the hours orders are sent, by the trader in charge **and** by the risk management or an independent risk control function. "Real-time alerts shall be generated within five seconds after the relevant event." Staff must "respond to operational and regulatory issues in a timely manner and shall initiate remedial action where necessary", and the competent authority, venues, DEA providers, clearing members and CCPs must "at all times have access to staff members in charge of real-time monitoring". | The five seconds is a budget for the **whole path** — metric computation, transport, this decision, notification. It is the only alert-timing figure in this skill with a regulatory source. The two-lines-of-defence structure is why a CRITICAL tier routes to compliance as well as to the desk. |
| **MiFID II RTS 6** | **Art. 15**, pre-trade controls on order entry | Price collars, maximum order value and volume, maximum message limits; the firm "shall automatically block or cancel orders where those orders risk compromising the investment firm's own risk thresholds". Overrides of blocked orders require risk-management verification and designated authorisation. | Confirms the escalation matrix is a *supplement*: the mandatory control blocks entry, this one responds after exposure exists. The override provision is the hook for `risk-control-bypass-audit-logging`. |
| **MiFID II RTS 6** | **Art. 12**, kill functionality | The firm "shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected". | `HALT` and `FLATTEN` are decisions; the mandated capability to execute them lives in the kill-switch skills. |
| **SEC Rule 15c3-5** — 17 CFR 240.15c3-5 (US broker-dealers with market access) | **(b)**, **(c)(1)(i)**, **(c)(1)(ii)** | A documented system of risk management controls "reasonably designed to manage the financial, regulatory, and other risks"; prevent entry of orders exceeding "appropriate pre-set credit or capital thresholds" by rejecting them; prevent erroneous orders by rejecting those exceeding price or size parameters. | The limits this matrix reacts to are the "pre-set thresholds". Note the verb: *reject*, pre-trade. |
| **SEC Rule 15c3-5** | **(d)** | The financial and regulatory risk management controls and supervisory procedures "shall be under the direct and exclusive control of the broker or dealer". | An escalation action routed through a vendor or a venue that the firm cannot itself invoke does not satisfy the rule. |
| **SEC Rule 15c3-5** | **(e)** | Effectiveness review "no less frequently than annual", with an annual CEO certification that the controls and procedures comply. | The escalation ladder and its thresholds are part of what is reviewed and certified — which is why the audit trail records the inputs behind each verdict, not only the verdict. |

**Jurisdiction:** RTS 6 binds EU investment firms engaged in algorithmic
trading (the UK applies an assimilated version through the FCA Handbook).
Rule 15c3-5 binds US broker-dealers with market access. A proprietary trading
firm outside both perimeters is bound by neither, and neither may be cited as
the source of a specific threshold value.

## 2. House defaults — engineering conventions, not standards

Nothing below has a regulatory or industry-standard source. They are this
skill's defaults, chosen to be defensible starting points, and they are meant to
be replaced by firm-specific calibration.

| Setting | Default | Status |
|---|---|---|
| Tier multipliers | 1.0x INFO/WARN, 1.2x AMBER/REDUCE, 1.5x RED/HALT, 2.0x CRITICAL/FLATTEN | House default. Calibrate against realised drawdown distributions — see `risk-limit-calibration-against-historical-drawdowns`. |
| Sustained-breach window | 300 s, inclusive (`duration >= 300.0`) | House default. Inclusive because fail-closed is the correct bias for a risk control; the boundary is a deliberate choice, not an off-by-one. |
| Acknowledgement deadlines | 900 / 300 / 120 / 60 s by tier | House default. No regulator prescribes a pager SLA. RTS 6 Art. 16 constrains *alert generation* (5 s), not human acknowledgement. |
| `LOWER`-direction ratio | $1 + (\text{limit} - \text{current}) / \text{limit}$, floored at 0 | House calibration so a single ladder serves both ceilings and floors. It places an exhausted buffer at 2.0, the same rung as a 2x ceiling breach. Defensible, but arbitrary — verify it suits your floor metrics before adopting it. |
| Escalation latching | On by default | House default. Justified because an oscillating metric would otherwise cancel an in-flight FLATTEN, but it means de-escalation requires an explicit, logged `reset_incident()`. |
| Replay cache | 10,000 events, FIFO | House default. Beyond it, the oldest fingerprints are evicted and a very late duplicate would be reprocessed. |

## 3. Deliberately unverified

- **No source was found for any regulatory or industry requirement that a
  sustained breach must auto-escalate after a specific interval.** The previous
  version of this file asserted "sustained breaches $>300$s MUST auto-escalate"
  and "CRITICAL breaches MUST require PagerDuty acknowledgment within $<60$s"
  as engineering standards. Both are house defaults; the MUST framing and the
  implied external authority have been removed.
- **PagerDuty and Slack are named as routing destinations only.** This skill
  integrates with neither, and no claim is made about either product's API,
  delivery semantics or escalation-policy behaviour.

## 4. Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589
- RTS 6 Art. 16, FCA-assimilated text — https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1568
- 17 CFR 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5
