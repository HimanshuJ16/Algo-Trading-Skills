# Standards — post-breach-root-cause-analysis-template

## Engine gates (house conventions — calibrate before use)

These are the engine's defaults. **None of them is a regulatory minimum.** No regulator,
exchange, or standards body publishes a mandatory 5-Whys depth, a mandatory RCA length, or a
universal RCA deadline for an ordinary trading firm. Set them to what your incident-management
policy actually says, and record the values with the report.

| Parameter | Default | What it does |
|---|---|---|
| `min_five_whys_depth` | $3$ | Levels of drill-down before the RCA counts as complete. On breach: `INSUFFICIENT_5_WHYS_DEPTH`. Blank entries raise rather than counting toward depth. |
| `require_preventive_action` | `False` | When `True`, an all-`CORRECTIVE` CAPA set is logged. Advisory by design: some incidents genuinely have no preventive action beyond the fix. `has_preventive_action` reports it either way. |
| `rca_due_by` | `None` (per incident) | Caller-supplied deadline. The engine does not know your regime and **will not invent a deadline**. On breach: `RCA_PAST_DUE`. |
| `possible_rule_violation` | `None` (per incident) | Must be set explicitly to `True` or `False`. `None` produces `RULE_VIOLATION_ASSESSMENT_MISSING`. Defaulting it to `False` would answer a legal question on the author's behalf. |

## Regulatory position (verified — read before citing anything)

### What does *not* require this artefact

| Claim often made | Actual text | Status |
|---|---|---|
| "SEC Rule 15c3-5 requires a post-breach RCA." | Rule 15c3-5(e): a broker-dealer "shall establish, document, and maintain a system for regularly reviewing the effectiveness of the risk management controls and supervisory procedures required by paragraphs (b) and (c) ... and for promptly addressing any issues." — [17 CFR 240.15c3-5](https://www.law.cornell.edu/cfr/text/17/240.15c3-5) | **False as stated.** Mandatory rule, but it is a *periodic review* obligation. The rule contains no incident-level RCA, post-mortem, or root-cause requirement. |
| "FINRA Rule 4511 requires an RCA." | Rule 4511(a)–(b): members "shall make and preserve books and records as required under the FINRA rules, the Exchange Act and the applicable Exchange Act rules", preserving for "at least six years" those records with no specified period. — [FINRA Rule 4511](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511) | **False as stated.** 4511 governs *preservation*, not creation. It is relevant in the other direction: once you write an RCA it is a business record subject to retention, in a format complying with SEA Rule 17a-4 (4511(c)). |
| "MiFID II RTS 6 requires a post-incident root cause analysis." | RTS 6 Art. 14 requires documented business continuity arrangements that "effectively deal with disruptive incidents", the ability to shut an algorithm down without creating disorderly trading, and an **annual** review and test of those arrangements. — [FCA Handbook, RTS 6 Art. 14](https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1566) | **Not supported.** Mandatory for EU/EEA (and, as assimilated law, UK) firms engaged in algorithmic trading, but the obligation is arrangements-plus-annual-review, not a per-incident RCA. Note also that DORA has displaced RTS 6 Arts. 14 and 18 from the annual self-assessment scope for EU firms. |

The defensible framing: a post-breach RCA is a **house control** that evidences the periodic
"reviewing the effectiveness ... and promptly addressing any issues" obligation in
15c3-5(e) and the equivalent EU/UK arrangements-review duties. It is not itself mandated by
those rules.

### What *does* attach deadlines — and to whom

| Obligation | Who it binds | Text / deadline | Source |
|---|---|---|---|
| **FINRA Rule 4530(b)** — self-reporting a concluded violation | FINRA member firms (US) | "Each member shall promptly report to FINRA, but in any event not later than **30 calendar days**, after the member has concluded or reasonably should have concluded that an associated person of the member or the member itself has violated any securities-, insurance-, commodities-, financial- or investment-related laws, rules, regulations or standards of conduct..." | [FINRA Rule 4530](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4530) |
| **Regulation SCI, 17 CFR 242.1002** — SCI event obligations | **SCI entities only**: exchanges, registered clearing agencies, FINRA/MSRB, plan processors, and ATSs exceeding the volume thresholds. **Not** ordinary broker-dealers or trading firms. | (a) begin appropriate corrective action, including mitigating potential harm and devoting adequate resources to remedy the event; (b)(2) written notification on Form SCI within **24 hours**; (b)(3) ongoing updates; (b)(4) interim report at **30 calendar days** if unresolved, final report within **5 business days** of resolution and closure of the investigation. De minimis events are recorded and summarised in **quarterly** reports instead. | [17 CFR 242.1002](https://www.law.cornell.edu/cfr/text/17/242.1002) |
| **DORA Art. 19(4)** — major ICT-related incident reporting | EU "financial entities" as defined in Regulation (EU) 2022/2554 Art. 2 — investment firms, trading venues, CCPs and others. Applies to **ICT-related** incidents, not to every P&L breach. | Three submissions to the competent authority: (a) an initial notification; (b) an intermediate report; (c) "a final report, **when the root cause analysis has been completed**, regardless of whether mitigation measures have already been implemented". | [DORA Art. 19](https://www.digital-operational-resilience-act.com/Article_19.html) (Regulation (EU) 2022/2554) |

Two points the engine deliberately encodes:

- **Regulation SCI's final report does not use the phrase "root cause".** 17 CFR
  242.1002(b)(4)(ii) requires a detailed description of affected market participants, market
  impact, steps taken, resolution time, relevant rules, and a loss analysis — not a root-cause
  section. Do not cite Reg SCI as an authority for the 5-Whys format.
- **DORA is the one regime above whose deadline is genuinely keyed to root-cause analysis**,
  and it applies to ICT-related incidents at EU financial entities. The precise clock for the
  final report is fixed by Commission Delegated Regulation (EU) 2025/301 Art. 5; confirm the
  current wording against the Official Journal before relying on a specific number of days.
  This engine takes the deadline as an input (`rca_due_by`) rather than deriving one.

Because the applicable deadline depends entirely on entity type and jurisdiction — 30 calendar
days for a FINRA member's 4530(b) report, 24 hours plus a 30-day interim for an SCI entity, a
staged sequence for an EU financial entity under DORA, and nothing at all for an unregulated
proprietary firm — **the engine refuses to supply a default.** A previous version of this skill
asserted a "3 business days from containment" standard. That figure had no source and has been
removed.

## Method

The 5-Whys technique originates in the Toyota Production System (Taiichi Ohno) and is a
facilitation heuristic, not a validated causal method. Repeating "why" five times does not
guarantee a root cause, and it biases toward a single causal chain when trading incidents are
usually multi-causal (a config change *and* an absent staging gate *and* an alert nobody
owned). Treat the chain as the minimum written record, not as the analysis.

CAPA (Corrective and Preventive Action) terminology is borrowed from quality-management
practice, where the distinction is the substance: a corrective action addresses the instance
that occurred, a preventive action addresses the class of failure. `has_preventive_action`
exists because an all-corrective CAPA set is the most common way a post-mortem produces no
durable change.

## Known limitations

- **No truth check.** Every gate is structural. A coherent, well-formatted, entirely fictional
  RCA passes all of them.
- **The blame heuristic is a substring match.** `TERMINAL_BLAME_ATTRIBUTION` matches a short
  phrase list against the final "why" only, case-insensitively. It is advisory, it misses
  paraphrases, and it occasionally fires on legitimate wording.
- **Financial figures are not computed or reconciled.** They are validated as finite,
  non-negative magnitudes and rendered; nothing more.
- **No filing.** The module produces a record. It notifies no regulator and files nothing.
- **Clock quality is recorded, not verified.** `TimelineEvent.source` documents which clock a
  timestamp came from. The engine cannot tell you whether that clock was disciplined.

## Category

`risk-governance` — see the top-level `mappings/` directory.
