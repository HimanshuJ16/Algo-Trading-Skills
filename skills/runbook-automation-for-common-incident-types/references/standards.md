# Standards for Runbook Automation for Common Incident Types

Every obligation below is quoted from the instrument named. Where this skill
uses a number that no regulator prescribes, it is labelled as an engineering
choice, not a standard.

## 1. What the automated remediation must be able to do

| Obligation | Instrument | Applies to | Text |
|---|---|---|---|
| Immediate cancellation of unexecuted orders | MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589, **Art. 12(1)** | EU investment firms engaged in algorithmic trading | "An investment firm shall be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders submitted to any or all trading venues to which the investment firm is connected ('kill functionality')." |
| Attribution of every order to an algorithm and a trader | RTS 6 **Art. 12(3)** | as above | "an investment firm shall be able to identify which trading algorithm and which trader, trading desk or, where applicable, which client is responsible for each order that has been sent to a trading venue." |
| A written usage policy for the kill functionality | RTS 6 **Art. 14(2)(e)** | as above | Business continuity arrangements shall include a "usage policy regarding the functionality referred to in Article 12". |
| Arrangements to shut the algorithm down | RTS 6 **Art. 14(2)(f)** | as above | "arrangements for shutting down the relevant trading algorithm or trading system where appropriate" |
| Arrangements for outstanding orders and positions | RTS 6 **Art. 14(2)(g)** | as above | "alternative arrangements for the investment firm to manage outstanding orders and positions" |
| Orderly shutdown | RTS 6 **Art. 14(3)** | as above | "An investment firm shall ensure that its trading algorithm or trading system can be shut down in accordance with its business continuity arrangements without creating disorderly trading conditions." |

**Implementation impact.** Art. 14(3) is the reason `halt_on_failure` defaults
to `False`. A playbook that abandons `TRIGGER_KILL_SWITCH` because
`CANCEL_OPEN_ORDERS` failed leaves the algorithm running through a breached
limit — the opposite of an orderly shutdown. Art. 12(3) is the reason the
engine records `source_service` and the full alert context in every report.

## 2. Testing and review cadence

| Obligation | Instrument | Text |
|---|---|---|
| Testing in a separated environment | RTS 6 **Art. 7** | "An investment firm shall ensure that testing of compliance with the criteria laid down in Article 5(4)(a), (b) and (d) is undertaken in an environment that is separated from its production environment." |
| Annual BCP review and test | RTS 6 **Art. 14(4)** | "An investment firm shall review and test its business continuity arrangements on an annual basis and modify the arrangements in light of that review." |
| Annual self-assessment | RTS 6 **Art. 9** | Annual review of algorithmic trading systems, governance and business continuity arrangements, validated, internally audited and approved by senior management. |
| Annual review of market-access controls | SEC **17 CFR 240.15c3-5(e)(1)** | Reviews of the effectiveness of the risk management controls "no less frequently than annually", documented and preserved. |
| CEO certification | SEC **17 CFR 240.15c3-5(e)(2)** | The CEO must "annually, certify that such risk management controls and supervisory procedures comply" with the rule. |

**Implementation impact.** Dry-run mode is an operational readiness check, not
the Art. 7 separated environment. Use it to prove wiring on the annual Art. 14(4)
test and in CI; do not present it as pre-deployment testing.

## 3. Incident recording

| Obligation | Instrument | Text |
|---|---|---|
| Record all incidents | DORA — Regulation (EU) 2022/2554, **Art. 17(2)** | "Financial entities shall record all ICT-related incidents and significant cyber threats." |
| Log and classify by priority and severity | DORA **Art. 17(3)(b)** | "establish procedures to identify, track, log, categorise and classify ICT-related incidents according to their priority and severity and according to the criticality of the services impacted" |
| Assign responses per incident type | DORA **Art. 17(3)(c)** | "assign roles and responsibilities that need to be activated for different ICT-related incident types and scenarios" |
| Response procedures that mitigate impact | DORA **Art. 17(3)(f)** | "establish ICT-related incident response procedures to mitigate impacts and ensure that services become operational and secure in a timely manner" |

DORA has applied since 17 January 2025 to EU financial entities within its
scope. Art. 17(3)(c) is the direct grounding for the per-incident-type playbook
table; Art. 17(2) is why `get_audit_history()` is documented as a debugging
convenience that must be persisted, not as the record itself.

## 4. What this skill is *not* evidence of

| Control | Instrument | Why this skill does not satisfy it |
|---|---|---|
| Pre-trade financial risk controls | SEC **17 CFR 240.15c3-5(c)(1)(i)–(ii)** — prevent orders exceeding "appropriate pre-set credit or capital thresholds", and prevent "erroneous orders, by rejecting orders that exceed appropriate price or size parameters" | These operate **before** an order is entered. This engine responds after an incident is already open. |
| Direct and exclusive control | SEC **17 CFR 240.15c3-5(d)** | Controls must be "under the direct and exclusive control of the broker or dealer". A remediation runbook whose actions are handlers you supply is not itself that control. |
| Real-time monitoring | RTS 6 **Art. 16** | "An investment firm shall … monitor in real time all algorithmic trading activity that takes place under its trading code." This engine consumes alerts; it does not monitor. |

**Jurisdiction note.** RTS 6 binds investment firms engaged in algorithmic
trading in the EU. Rule 15c3-5 binds US broker-dealers with market access, not
every proprietary trading firm. DORA binds EU financial entities in its scope.
Determine which apply to your entity before citing any of them in an internal
control document.

## 5. Latency budgets — engineering, not regulation

The 1.0.0 version of this file carried a "Max Execution SLA" column
(`< 500 ms`, `< 100 ms`, `< 1000 ms`, `< 50 ms`) with no source. **Those numbers
were not traceable to any standard and have been removed.** No regulator
prescribes a remediation-execution deadline.

The nearest genuine timing obligation is on a *different* event:

> "Real-time alerts shall be generated within five seconds after the relevant
> event." — RTS 6 **Art. 16**

That is alert generation, upstream of this engine, not remediation execution.

What to do instead: set a per-step budget you can defend from your own measured
handler latencies and your broker's documented API timeouts, record why you
chose it, and enforce it with `step_timeout_seconds`. The module default of
30 seconds is a backstop against a stalled capital-protection sequence, chosen
so that one unresponsive handler cannot block the rest of the playbook. It is
not a target and it is not a standard.

## 6. The case study behind the design

SEC Administrative Proceeding **34-70694**, *In the Matter of Knight Capital
Americas LLC* (16 October 2013), settled for $12 million following a 45-minute
event on 1 August 2012 in which roughly $460 million was lost. Two findings
shape this engine directly:

> "Knight did not have supervisory procedures concerning incident response. More
> specifically, Knight did not have supervisory procedures to guide its relevant
> personnel when significant issues developed."

> "In one of its attempts to address the problem, Knight uninstalled the new RLP
> code from the seven servers where it had been deployed correctly. This action
> worsened the problem, causing additional incoming parent orders to activate the
> Power Peg code that was present on those servers, similar to what had already
> occurred on the eighth server."

The first is the argument for having a runbook. The second is the argument for
refusing to run one on a diagnosis you do not have — which is why an unmapped
`incident_type` escalates with zero steps rather than falling back to
`CANCEL_OPEN_ORDERS`.

## 7. Engineering practice

> "When humans are necessary, we have found that thinking through and recording
> the best practices ahead of time in a 'playbook' produces roughly a 3x
> improvement in MTTR as compared to the strategy of 'winging it'."
> — Beyer, Jones, Petoff & Murphy, *Site Reliability Engineering* (O'Reilly,
> 2016), Introduction.

The same passage sets the limit: "While no playbook, no matter how comprehensive
it may be, is a substitute for smart engineers able to think on the fly …". The
engine escalates rather than improvising precisely so that a human gets the
cases the playbook does not cover.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) —
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589
- Regulation (EU) 2022/2554 (DORA) —
  https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32022R2554
- 17 CFR 240.15c3-5 (Market Access Rule) —
  https://www.law.cornell.edu/cfr/text/17/240.15c3-5
- SEC Admin. Proc. 34-70694, *In the Matter of Knight Capital Americas LLC* —
  https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf
- Beyer et al., *Site Reliability Engineering*, Introduction —
  https://sre.google/sre-book/introduction/
