# Standards for On-Call Rotation and Escalation for Trading Systems

## 1. What is mandatory, and what is only convention

The single most important distinction in this skill: **no regulator prescribes a
5-minute pager SLA.** The severity SLAs this engine enforces are engineering
conventions a firm chooses and must then defend. What regulators do mandate is
that the responsible staff are *reachable*, that the contact procedure is
*tested*, and that separate reporting clocks start when an incident is
classified — independently of how fast the pager ladder runs.

| Claim | Status | Source |
|---|---|---|
| SEV-1 acknowledged within 5 min; low-priority within 30 min | **Engineering convention**, not a rule | Google SRE book, ch. 11 |
| Staff in charge of real-time monitoring reachable at all times, out-of-hours contact procedures periodically tested | **Mandatory** (EU, algorithmic trading investment firms) | RTS 6 Art. 16(4) |
| Business continuity arrangements covering unavailability of staff | **Mandatory** (EU, same firms) | RTS 6 Art. 14 |
| Major ICT incident: initial notification within 4 h of classification, and no later than 24 h from awareness | **Mandatory** (EU financial entities) | DORA RTS, Art. 5 |
| SCI event notified to the SEC immediately, written notice within 24 h | **Mandatory — but only for SCI entities** | 17 CFR 242.1002(b) |

## 2. EU — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

Applies to investment firms engaged in algorithmic trading. This is the
provision that makes an on-call rotation a regulatory artifact rather than an
internal convenience.

**Article 16(3) — Real-time monitoring:**

> "Staff members in charge of the real-time monitoring shall respond to
> operational and regulatory issues in a timely manner and shall initiate
> remedial action where necessary."

**Article 16(4):**

> "An investment firm shall ensure that the competent authority, the relevant
> trading venues and, where applicable, DEA providers, clearing members and
> central counterparties can at all times have access to staff members in charge
> of real-time monitoring. For that purpose, the investment firm shall identify
> and periodically test its communication channels, including its contact
> procedures for out of trading hours, to ensure that in an emergency the staff
> members with the adequate level of authority may reach each other in time."

Note the obligation is *reachability with tested channels*, not a numeric
response time. Two engine behaviours map directly onto it: an escalation to an
unstaffed tier or a hole in the rota is reported with
`is_notification_deliverable=False` rather than silently absorbed, and an
engineer with no contact address for the required channel is flagged. "Periodic
testing" of the channel itself is out of scope for this engine — it computes who
should be paged, it does not send anything.

**Article 14 — Business continuity arrangements** requires documented
arrangements covering "a range of possible adverse scenarios ... including the
unavailability of systems, **staff**, work space, external suppliers or data
centres". An unstaffed rotation tier is exactly that scenario.

Source: [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng)
(RTS 6), Arts. 14 and 16.

## 3. EU — DORA incident reporting clocks

Regulation (EU) 2022/2554 (DORA) applies to financial entities from
17 January 2025. Commission Delegated Regulation (EU) 2025/301 of 23 October
2024, Article 5, sets the reporting deadlines for a **major** ICT-related
incident:

| Report | Deadline |
|---|---|
| Initial notification | As early as possible, within **4 hours of classification** as major, and no later than **24 hours from becoming aware** of the incident |
| Intermediate report | Within **72 hours** of the initial notification, even if nothing has changed |
| Final report | No later than **one month** after the intermediate (or latest updated intermediate) report |

Two consequences for escalation design:

1. **The reporting clock is not the pager clock.** It starts at awareness and
   classification, not at acknowledgement. A SEV-1 acknowledged in two minutes
   can still breach a DORA deadline if nobody classified it.
2. **The weekend extension is not universal.** Article 5 allows deadlines
   falling on a weekend or bank holiday to move to noon the next working day,
   but that extension explicitly does *not* apply to credit institutions,
   central counterparties, trading venue operators, or entities designated
   essential/important under the NIS 2 Directive. Do not encode a blanket
   weekend extension.

Source: [Commission Delegated Regulation (EU) 2025/301](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202500301), Art. 5.

## 4. US — Regulation SCI: check applicability before assuming it applies

Reg SCI imposes the tightest notification requirement in this table — under
17 CFR 242.1002(b), on any responsible SCI personnel having a reasonable basis
to conclude an SCI event occurred, the entity notifies the Commission
**immediately**, followed by written notification on Form SCI **within 24
hours** (a de minimis exception applies where the event has no or de minimis
impact).

It also has the narrowest scope. Under 17 CFR 242.1000, "SCI entity" means "an
SCI self-regulatory organization, SCI alternative trading system, plan
processor, exempt clearing agency subject to ARP, or SCI competing
consolidator", where "SCI self-regulatory organization" means a national
securities exchange, registered securities association, registered clearing
agency, or the MSRB.

**An ordinary broker-dealer or a proprietary algorithmic trading firm is not an
SCI entity.** A 2023 proposal would have widened the definition to cover certain
large broker-dealers; the SEC formally withdrew it in June 2025 and stated it
does not intend to issue final rules on the withdrawn proposals. Applying Reg
SCI timelines to a firm that is not an SCI entity is a real and common error —
it manufactures an obligation that does not exist and distorts the escalation
design around it.

Sources: [17 CFR 242.1000](https://www.law.cornell.edu/cfr/text/17/242.1000),
[17 CFR 242.1002](https://www.law.cornell.edu/cfr/text/17/242.1002).

## 5. Engineering conventions (defensible defaults, not rules)

Response SLA defaults come from Beyer et al., *Site Reliability Engineering*
(O'Reilly, 2016), ch. 11 "Being On-Call":

> "Typical values are 5 minutes for user-facing or otherwise highly
> time-critical services, and 30 minutes for less time-sensitive systems."

The same chapter describes the primary/secondary structure this engine
implements — "Many teams have both a primary and a secondary on-call rotation",
with one common arrangement using "the secondary as a fall-through for the pages
the primary on-call misses" — and gives a workload ceiling of **2 incidents per
12-hour on-call shift**, above which incidents cannot be handled with the care
they need.

Source: [Google SRE Book, "Being On-Call"](https://sre.google/sre-book/being-on-call/).

## 6. Engine defaults

| Setting | Default | Basis |
|---|---|---|
| SEV-1 response SLA | 5 min | Google SRE convention for time-critical services |
| SEV-2 response SLA | 15 min | House convention; between the two SRE reference values |
| SEV-3 response SLA | 60 min | House convention; deliberately looser than the SRE 30 min for non-time-sensitive systems |
| SEV-1 ladder | PRIMARY t=0 → SECONDARY t=3 → EXECUTIVE t=5 | Chosen so the executive rung coincides with the SLA |
| SEV-2 ladder | PRIMARY t=0 → SECONDARY t=10 → EXECUTIVE t=30 | House convention |
| SEV-3 ladder | PRIMARY t=0 → SECONDARY t=30, no executive rung | Alert-fatigue control |
| Acknowledgement timeout | 30 min, enabled | See below |
| Unknown severity | Treated as SEV_1, logged at ERROR | Fail loud and safe, never downward |

All thresholds are **cumulative minutes since incident creation**. PagerDuty's
"escalates after N min" is the opposite convention — the time a responder has at
that level, measured from when that level was notified. Convert before
transposing a PagerDuty policy into this config.

The acknowledgement timeout is enabled by default here. PagerDuty offers the
same mechanism ("An acknowledged incident re-triggers after a specified amount
of time") but ships it **off** by default; for a live trading system the failure
it prevents — an acknowledged fault silently abandoned — is more expensive than
an extra page.

Sources: [PagerDuty, Escalation Policy Basics](https://support.pagerduty.com/main/docs/escalation-policies),
[PagerDuty, Configurable Service Settings](https://support.pagerduty.com/main/docs/configurable-service-settings).

## 7. Not verified / out of scope

- No jurisdiction-specific pager SLA has been identified for UK, US, Indian or
  Singaporean algorithmic trading firms. If your compliance function asserts
  one, it is a house or exchange-membership obligation — record the source next
  to the threshold in your config rather than assuming it is statutory.
- Retention periods for incident records are not set by this skill. See
  `record-retention-periods-by-jurisdiction`.
