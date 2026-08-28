# Standards — regulatory-change-monitoring-service-integration

## Configuration defaults (house settings, calibrate before use)

**No regulator publishes a required polling frequency, alert window, or severity
taxonomy for regulatory-change monitoring.** The values below are this engine's
defaults. Treat them as firm policy you must be able to defend, not as
compliance thresholds, and record the values used alongside each report.

| Parameter | Default | What it actually does |
|---|---|---|
| `monitored_regulators` | `("SEC","FCA","SEBI","ESMA","MAS")` | Case-insensitive authority filter. Non-matching updates are counted in `filtered_regulator_count`, never silently dropped. An explicitly empty sequence raises. |
| `urgent_action_window_days` | $30$ | Days-to-deadline at or below which an **open** CRITICAL/HIGH item escalates. Compared inclusively: exactly 30 escalates, 31 does not. House escalation policy. |
| `monitored_subdomains` | `None` (retain all) | Optional subject-area filter. An update with **no** `impacted_subdomains` is retained regardless — fail-open, logged at WARNING. |
| `current_date_iso` | today (UTC) | Assessment date. Pass explicitly for reproducible audits; the default is logged. |

Escalation rules that are *not* configurable, by design:

- An **open item past its binding deadline escalates at any severity.** A missed
  deadline is a live breach, not a forecast, so it does not sit behind a
  severity gate.
- A **malformed date raises.** No placeholder deadline is ever written to the
  audit record.
- An **unrecognised severity raises.** Nothing is defaulted to `LOW`.

## Effective date vs compliance date (verified against primary sources)

| Fact | Source | Applied here |
|---|---|---|
| Federal Register document 2023-03566, "Shortening the Securities Transaction Settlement Cycle": `publication_date` 2023-03-06, `effective_on` **2023-05-05**, and the structured `dates` field reads only "Effective date: May 5, 2023." | [Federal Register API](https://www.federalregister.gov/api/v1/documents/2023-03566.json) (queried 2026-08-27) | Demonstrates that the structured feed exposes **no compliance-date field**. An adapter that maps `effective_on → effective_date` and stops has lost the deadline. |
| "The final rules will become effective 60 days after publication in the Federal Register. **The compliance date for the final rules is May 28, 2024.**" | [SEC press release 2023-29](https://www.sec.gov/newsroom/press-releases/2023-29), 15 Feb 2023 (adopting release 34-96930) | The compliance date — 419 days after the effective date — is the deadline the engine uses when `compliance_date` is populated. |
| MiFID II (Directive 2014/65/EU) was published in the OJ on 2014-06-12, entered into force 20 days later on 2014-07-02, and **applies from 2018-01-03**, the application date having been postponed by one year by Directive (EU) 2016/1034. | [EUR-Lex summary, "Better regulated and transparent financial markets"](https://eur-lex.europa.eu/EN/legal-content/summary/better-regulated-and-transparent-financial-markets.html) | The EU equivalent of the same split ("entry into force" vs "date of application"), and evidence that **deadlines move**. Revised records must replace, not duplicate, the original. |

Applicability note: the compliance-date pattern is a feature of US notice-and-comment
rulemaking and EU legislative instruments. FCA Handbook changes carry commencement
dates set in the relevant Handbook Notice; SEBI circulars state applicability in the
circular text; MAS notices state their own effective dates. **Verify the deadline in
the source instrument for each jurisdiction — do not infer it from the feed schema.**

## Regulatory position — what is actually mandatory

Monitoring for regulatory change is not itself a named rule in any of the
jurisdictions this skill covers. It is how firms discharge broader, genuinely
mandatory supervisory obligations:

| Claim | Source | Status |
|---|---|---|
| Each member must establish and maintain a supervisory system, and "establish, maintain, and enforce written procedures to supervise the types of business in which it engages … reasonably designed to achieve compliance with applicable securities laws and regulations, and with applicable FINRA rules." | [FINRA Rule 3110(a), 3110(b)(1)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/3110) | **Mandatory** (FINRA member firms, US). Procedures must track *applicable* rules — which is only possible if changes to them are tracked. **No monitoring cadence, tooling, or alert window is specified.** |
| An investment firm "shall annually perform a self-assessment and validation process and on the basis of that process issue a validation report", reviewing its algorithmic trading systems, governance and approval framework, business continuity arrangements, and "its overall compliance with Article 17 of Directive 2014/65/EU". The validation report is drawn up by the risk management function, involving staff with the necessary technical knowledge. | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Art. 9 — [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng) | **Mandatory** (EU/EEA investment firms engaged in algorithmic trading). Makes "are we still compliant with current obligations" an annual, documented exercise. **Annual is the floor; it does not mandate continuous monitoring.** |

Defensible framing: continuous regulatory-change monitoring is a **house control
supporting** FINRA Rule 3110 supervisory procedures and the RTS 6 Art. 9 annual
self-assessment. It is not itself a regulatory requirement, and the 30-day window
and 24-hour poll cadence are not derived from one. Do not write "required by
FINRA/ESMA" against either number.

## Feed integration notes (verified)

- The **Federal Register API** (`https://www.federalregister.gov/api/v1/documents.json`)
  filters SEC rules by `conditions[agencies][]=securities-and-exchange-commission`
  and `conditions[type][]=RULE`, and exposes `document_number`, `publication_date`,
  `effective_on`, `dates`, and `html_url`. Verified 2026-08-27. There is **no
  structured compliance-date field**; compliance dates must be extracted from the
  document body or supplied by an analyst.
- `document_number` is a stable identifier and is the natural `update_id` for SEC
  rulemakings. Equivalents: FCA Handbook Notice number, SEBI circular reference,
  EUR-Lex CELEX number, MAS notice number.
- Rate limits, authentication requirements, and terms of use of each publisher's
  feed were **not verified for this skill** and must be checked before deploying a
  poller. Do not assume the 24-hour cadence below is permitted by the publisher.

| Metric | Engineering standard (house policy) |
|---|---|
| Poll cadence | Regulatory RSS/API feeds SHOULD be polled at least once every $24$ hours. House target; no regulator specifies a frequency and no publisher SLA has been verified. |
| Urgent action window | Open CRITICAL/HIGH updates within $\le 30$ calendar days of the **binding deadline** escalate immediately, as do all overdue open items. House escalation policy. |
| Audit trail | Every assessment cycle's `RegulatoryChangeReport` — including the assessment date, filter counts, and filtered authority names — SHOULD be persisted for the firm's applicable record-retention period. Retention periods themselves are jurisdiction-specific; see `record-retention-periods-by-jurisdiction`. |

## Known limitations

- **Calendar days, not business days.** No holiday calendar and no
  jurisdiction-local timezone; a 30-day window may contain 19 or 23 working days.
- **No legal interpretation.** Severity, action-required, and subject-area labels
  are caller-supplied.
- **One deadline per update.** Phased rulemakings need one record per phase.
- **No supersession graph.** Revisions are handled by replacing the record
  upstream; the engine only guarantees a duplicate `update_id` cannot be
  double-counted inside one batch.
- **`COMPLIANT` is an assertion, not evidence.** It reflects the caller setting
  `remediation_complete`; nothing here verifies the remediation.
- **Ingestion is out of scope.** No fetching, authentication, parsing, or
  retry/backoff logic lives in this skill.

## Category

`regulatory-compliance-global` — see top-level `mappings/` directory.
