# Standards for Post-Mortem Culture and Blameless Review Process

Two different kinds of rule are mixed together in most blameless-post-mortem
write-ups: **binding obligations** that a regulator can examine you against,
and **practice conventions** from the SRE literature that carry no legal force.
They are separated here, because presenting a house convention as a mandate is
the fastest way to lose an argument with a compliance function — and presenting
a mandate as a convention is worse.

## 1. Binding obligations (jurisdiction-specific)

| Jurisdiction / regime | Instrument | What is actually required | Applies to |
|---|---|---|---|
| EU | Regulation (EU) 2022/2554 (DORA), **Art. 13(2)** | A **post ICT-related incident review** after a major ICT-related incident that has disrupted core activities, analysing the causes of disruption and identifying required improvements. The review must determine whether established procedures were followed and whether actions were effective as to (a) promptness of response and impact/severity determination, (b) quality and speed of forensic analysis where appropriate, (c) effectiveness of incident escalation, (d) effectiveness of internal and external communication. Non-microenterprises must communicate the resulting changes to competent authorities on request. | EU financial entities in scope of DORA, including MiFID investment firms. Applicable since 17 Jan 2025. |
| EU | Regulation (EU) 2022/2554 (DORA), **Art. 13(3)** | Lessons from resilience testing, real incidents, cyber-attacks, business-continuity activations and supervisory review must be **continuously incorporated** into the ICT risk management framework. A post-mortem whose actions are never fed back is not compliant with this paragraph. | As above. |
| EU | Commission Delegated Regulation (EU) 2017/589 (MiFID II **RTS 6**), **Art. 9** | An **annual self-assessment and validation** covering algorithmic trading systems, strategies, the governance/accountability/approval framework, business continuity arrangements, and overall compliance with Art. 17 MiFID II; the resulting validation report is audited by internal audit where that function exists, approved by senior management, and identified deficiencies must be remedied. Post-mortem findings are natural inputs to it. | Investment firms engaged in algorithmic trading in the EU. |
| US | 17 CFR § 242.1002(b)(4) (**Regulation SCI**) | Where an SCI event is resolved and its investigation closed within 30 calendar days, a **final written notification** to the SEC within **five business days** of resolution and closure, describing the entity's assessment of affected market participants, market impact, steps taken, resolution time, relevant rules/governing documents, and any other pertinent information. | **SCI entities only** (SROs, certain ATSs, plan processors, exempt clearing agencies) — *not* a typical proprietary or buy-side algorithmic trading firm. Do not apply this deadline to a firm that is not an SCI entity. |

Regulation SCI does **not** use the phrase "root cause analysis" in
§ 242.1002(b), and neither DORA nor RTS 6 mandates that a review be
*blameless*. Blamelessness is a method for getting an accurate account of what
happened; the regulatory obligation is to produce the account and act on it.

## 2. Practice references (not binding)

| Source | What it establishes |
|---|---|
| Beyer et al., *Site Reliability Engineering*, **Ch. 15, "Postmortem Culture: Learning from Failure"** (O'Reilly, 2016) — https://sre.google/sre-book/postmortem-culture/ | A blameless post-mortem focuses on contributing causes without indicting any individual or team, and assumes everyone involved acted with good intentions on the best information available at the time. Review criteria include whether the root cause was "sufficiently deep" and whether effective preventive actions were put in place. The chapter specifies **no** completion deadline and **no** minimum count of action items or contributing factors. |
| *The Site Reliability Workbook*, **Ch. 10, "Postmortem Culture: Learning from Failure"** — https://sre.google/workbook/postmortem-culture/ | Blameless does not mean "no consequences" or "anything goes". |
| John Allspaw, "Blameless PostMortems and a Just Culture", Etsy Code as Craft, May 2012 — https://www.etsy.com/codeascraft/blameless-postmortems | The "second story": the account beneath the surface explanation. Counterfactual phrasing ("X should have noticed Y") describes a history that did not happen and displaces the account of what actually did. Etsy's stated practice is to gather details from multiple perspectives and not punish people for mistakes. |

## 3. House defaults implemented by this skill

These are **configurable defaults chosen by this repository**, not standards.
Any of them can be defended or overridden; none can be cited to a regulator.

| Setting | Default | Rationale |
|---|---|---|
| `Config.min_systemic_factors` | `2` | One contributing factor is usually the trigger restated. Requiring a second forces at least one statement about the conditions that let the trigger reach production. |
| `Config.min_corrective_actions` | `1` | A post-mortem with no CAPA item records an incident without reducing recurrence. Enforced as `POSTMORTEM_INCOMPLETE`, not as an exception. |
| `Config.strict_blame_check` | `True` | Blame terms block approval. Set `False` for an advisory-only pass; findings are still reported as advisories, never discarded. |
| Blameless review meeting SLA | **Not enforced in code.** | No source consulted sets one. If your firm adopts a deadline (five business days is a common house choice, and coincides with the Reg SCI § 242.1002(b)(4) filing window for SCI entities), record it as internal policy and state it as such. |
| Counterfactual phrasing | Advisory only | "The alert should have fired" is legitimate about a system. A blocking check here produces false positives and trains reviewers to disable the whole screen. |

## 4. Known limits of the screen

- It is **lexical**. "The individual responsible for the release did not perform the verification step" contains no listed term and passes, while remaining squarely blameful. Human review remains the control; this tool removes the obvious cases.
- Terms embedded in established engineering vocabulary (`fault tolerance`, `fault injection`, `lazy loading`, `segmentation fault`, `default`) are exempted. The exemption list is finite; a new technical term containing a blame token will produce a false positive until it is added.
- It is English-only. Non-English narratives pass unscreened.
