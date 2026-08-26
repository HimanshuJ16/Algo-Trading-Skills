# Standards for Model Card Documentation

## Engineering requirements

| Requirement | Standard | Source |
|---|---|---|
| No compliance verdict | The engine MUST report documentation completeness only. It MUST NOT assert that a model or firm is compliant with any supervisory guidance or regulation. | SR 26-2, section I (guidance is non-binding) |
| Fail-closed governance defaults | Governance fields MUST default to unasserted. A caller who supplies no governance information MUST NOT receive a card claiming a validation occurred. | Repository mandate |
| Findings always surfaced | Every advisory finding computed MUST appear in the report and in the rendered card, including on a complete card. | Repository mandate |
| Numeric integrity | Every metric MUST be finite and within its admissible range. Non-finite values MUST raise, never be compared. | Repository mandate |
| Determinism | An identical call MUST produce an identical card. No wall-clock reads; staleness is measured only against a caller-supplied `as_of_date`. | Repository mandate |
| Thresholds are firm policy | Performance thresholds MUST be caller-owned and labelled as having no regulatory basis. | SR 26-2 prescribes no such thresholds |
| Artefact integrity | Caller-supplied text MUST be escaped so it cannot forge headings, table rows or list items in an audit artefact. | Repository mandate |
| Out-of-scope use documented | A card MUST NOT be reported complete while its out-of-scope uses are blank. | SR 26-2, section IV; Mitchell et al. (2019), §4.2 |
| Feature lineage documented | A card MUST NOT be reported complete while training sources, feature transformations, label definition or retraining cadence are blank. | Mitchell et al. (2019), §4.6 |

## Verified sources

**Board of Governors of the Federal Reserve System, FDIC, and OCC (2026). "Supervisory Guidance on Model Risk Management," attachment to SR letter 26-2, April 17, 2026.** <https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm>

Supersedes SR 11-7 (April 4, 2011) and SR 21-8 (April 9, 2021). The provisions this skill relies on:

- *Section I* — **non-binding**: "This guidance does not set forth enforceable standards or prescriptive requirements; accordingly, non-compliance with this guidance will not result in supervisory criticism against a banking organization." A footnote adds that supervisory action may still follow "for any violations of law or unsafe or unsound practices stemming from insufficient management of model risk."
- *Section II* — **audience**: "This guidance is expected to be most relevant to banking organizations with over $30 billion in total assets," while noting it may also be relevant to smaller banking organizations with significant model risk exposure.
- *Section II* — **definition of a model**: "a complex quantitative method, system, or approach that applies statistical, economic, or financial theories to process input data into quantitative estimates," excluding "simple arithmetic calculations" and "deterministic rule-based processes and software where there are no statistical, economic, or financial theories underpinning their design or use."
- *Section III, footnote 3* — **AI scope**: "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance," though "the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models."
- *Section IV* — **intended use**: "Using a model beyond its intended purpose introduces additional uncertainty and risk."
- *Section V* — **validation independence, softened relative to SR 11-7**: "The quality of validation process depends on the rigor and effectiveness of the review rather than on organizational structure of the banking organization's risk management function." Validation "generally occurs prior to a model's first use," but "certain circumstances (e.g., an urgent business need) may necessitate using the model before validation is completed," with compensating controls.
- *Section V* — **ongoing monitoring** is a named component of validation, alongside conceptual soundness and outcomes analysis.
- *Section VI* — **documentation**, in full: "Adequate documentation helps to support effective model risk management. For example, documentation can help maximize the likelihood of continuity of operations, including supporting the tracking of recommendations, responses, and exceptions; it can also be used to more effectively help manage any model remediation efforts."

> **Note on what SR 26-2 does not contain.** It prescribes no model card format, no mandatory section list, no Sharpe ratio floor, no drawdown ceiling and no capacity threshold. Its documentation section is one short paragraph and is permissive. Any numeric gate in `ReviewThresholds` is firm policy.

**Commission Delegated Regulation (EU) 2017/589 of 19 July 2016 (MiFID II RTS 6) — organisational requirements of investment firms engaged in algorithmic trading.**

- *Article 5* — prior to deployment or substantial update of an algorithmic trading system, algorithm or strategy, a firm must establish clearly delineated development and testing methodologies addressing the design, performance, recordkeeping and approval of that system, algorithm or strategy.
- *Article 9* — annual self-assessment and validation, and issuance of a validation report. Per ESMA (below), the process reviews "(a) its algorithmic trading systems, trading algorithms and algorithmic trading strategies; (b) its governance, accountability and approval framework; (c) its business continuity arrangement; (d) its overall compliance with Article 17 of MiFID II." Article 9(2)–(5) places primary responsibility with the risk management function, with internal audit review where applicable and senior management approval.

Unlike SR 26-2, RTS 6 is directly applicable law for in-scope EU investment firms. This is the obligation most likely to actually bind a non-bank algorithmic trading firm, and a model card is an input to it rather than a discharge of it.

**ESMA (2026). "Supervisory Briefing on Algorithmic Trading in the EU," ESMA74-1505669079-10311, 26 February 2026.** <https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf>

Explicitly non-binding ("The content of this briefing is non-binding and not subject to a 'comply or explain' mechanism"), but it states current supervisory expectations:

- ¶29 — "Investment firms need to ensure that testing methodologies, procedures and internal authorisations to deploy algorithmic trading are well documented... firms should thus keep the documentation comprehensive and updated."
- ¶31 — "A material change or substantial update is any modification that may alter the behaviour, risk profile, or compliance posture" of an algorithm; firms "are required to timestamp, approve, and record all material changes."
- ¶30, ¶47 — firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested." This is the direct justification for regenerating a card on material change rather than editing one in place.
- ¶46 — "AI-based algorithmic trading is currently excluded from the scope as a high-risk use case under the AI Act," though that scope is subject to annual review, and Article 50 transparency obligations may apply where a system is "intended to interact directly with natural persons."
- ¶50 — under RTS 6 Article 2, compliance staff must have at least a general understanding of how the firm's algorithms operate; "the algorithmic trading systems and algorithms should be explainable."
- ¶51 — "Investment firms shall conduct and document the required self-assessment on an article-by-article basis, covering all relevant articles of RTS 6."

**Mitchell, M., Wu, S., Zaldivar, A., Barnes, P., Vasserman, L., Hutchinson, B., Spitzer, E., Raji, I. D. & Gebru, T. (2019). "Model Cards for Model Reporting." _FAT* '19_, Atlanta, GA.** <https://arxiv.org/abs/1810.03993>

The origin of the model card format. Proposes nine sections — Model Details, Intended Use, Factors, Metrics, Evaluation Data, Training Data, Quantitative Analyses, Ethical Considerations, Caveats and Recommendations — and states they "may be tailored depending on the model, context, and stakeholders."

- *§4.2* — "Out-of-scope uses: Here, the model card should highlight technology that the model might easily be confused with, or related contexts that users could try to apply the model to," a section "inspired by warning labels on food and toys."
- *§4.9* — Caveats and Recommendations covers "additional concerns that were not covered in the previous sections."

`REQUIRED_SECTIONS` is this skill's tailoring of that taxonomy for trading models. It is a repository convention, not a published standard, and no external body requires six sections or these six.

## Stated limitations

1. **Completeness is not correctness.** The engine verifies that a section has content. It cannot verify the content is accurate, that the validation described took place, that the validator was independent, or that the metrics came from the model being documented. A card fully populated with fiction reports `MODEL_CARD_COMPLETE`.
2. **It issues no regulatory verdict, and cannot.** Whether SR 26-2, RTS 6, or neither applies to your firm depends on your legal entity, jurisdiction and licences — facts the engine has no access to. `applicable_frameworks` is rendered verbatim and never adjudicated.
3. **Thresholds are unsourced by design.** `ReviewThresholds` defaults (Sharpe ≥ 1.0, drawdown ≤ 25%, 365-day revalidation) are illustrative firm policy. Only the 365-day cadence has an external anchor, in the RTS 6 Article 9 annual requirement, and that applies only to in-scope EU investment firms.
4. **No cross-field consistency checking.** The engine does not verify that the evaluation window follows the training window, that the reported Sharpe is consistent with the reported return and drawdown, or that the kill-switch triggers are implemented anywhere. Those remain human review steps.
5. **Escaping protects structure, not meaning.** `_escape_md` prevents caller text from forging headings, table rows and list items. It does not detect misleading text placed in the correct section.
6. **Staleness is opt-in.** Omitting `as_of_date` skips the check entirely. This preserves determinism and reproducibility of the artefact, at the cost of silence about an ageing validation unless the caller asks.
7. **Single-model scope.** The engine documents one model. SR 26-2 section III notes that "sound practice involves assessing model risk both individually and in aggregate," reflecting "interactions and dependencies among models" — aggregate model risk and the model inventory are out of scope here.
