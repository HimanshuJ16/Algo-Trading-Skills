# Standards — explainable-boosting-machines-for-regulated-signals

## Read this first

No regulator has published a requirement that "EBM signals MUST provide exact,
un-approximated feature attributions" — not under SR 11-7 model governance, not
anywhere else. What the sources actually say is quoted below.

Two things to keep straight:

1. **US model-risk guidance is not a rule.** It is supervisory guidance addressed to
   banking organizations, and it says so explicitly.
2. **The EU explainability expectation is real but indirect.** It is derived from a
   compliance-staff-competence article, and the document deriving it is a non-binding
   convergence tool.

## United States — model risk management

**Current instrument:** Federal Reserve **SR 26-2**, *Revised Guidance on Model Risk
Management*, 17 April 2026, issued jointly by the Board of Governors, the OCC and the
FDIC. Companion: **OCC Bulletin 2026-13**, *Model Risk Management: Revised Guidance*
(same date).
<https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm> ·
<https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html>

**Superseded:** SR 26-2 states it "supersedes and replaces SR letter 11-7, *Guidance on
Model Risk Management* (issued April 4, 2011) and SR letter 21-8". OCC Bulletin 2026-13
rescinds OCC Bulletin 2011-12. Documentation citing SR 11-7 or OCC 2011-12 as the
governing standard is out of date as of 17 April 2026; keep the reference only where
you are describing a record produced under the predecessor guidance.

| Point | What the source says (SR 26-2 attachment) |
|---|---|
| Legal status | "This guidance does not set forth enforceable standards or prescriptive requirements; accordingly, non-compliance with this guidance will not result in supervisory criticism against a banking organization." |
| Who it addresses | "This letter is expected to be most relevant to banking organizations with over $30 billion in total assets regulated by the Federal Reserve." It is **not** addressed to proprietary trading firms, hedge funds or non-bank broker-dealers. |
| Whether an EBM is in scope | Footnote 3: "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance… However, the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models." An EBM is such a model. |
| Where interpretability appears | Under *Components of Model Validation — Conceptual Soundness*: "While evaluating theoretical construction may be important for some models, other assessments—such as **interpretability measures** or benchmarking to other models—may be more practical for other models." Interpretability is offered as one practical assessment, not required. |
| Documentation | "Adequate documentation helps to support effective model risk management." Permissive language; no prescribed document set. |
| Monotonicity | Not mentioned anywhere in the guidance. Monotonicity in this skill is a **business-domain constraint the model owner declares and the engine verifies**, not a supervisory requirement. |

## European Union — algorithmic trading

**Instrument:** Commission Delegated Regulation (EU) **2017/589** (MiFID II RTS 6),
organisational requirements for investment firms engaged in algorithmic trading.
This *is* binding law, and it applies to investment firms — not to models as such.

**Interpretive source:** ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*,
**ESMA74-1505669079-10311**, February 2026.
<https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf>

Its own status, para. 3: "This supervisory briefing is issued under Article 29(2) of the
ESMA Regulation, which enables ESMA to develop practical convergence tools such as
supervisory briefings. **The content of this briefing is non-binding and not subject to
a 'comply or explain' mechanism.**"

| Point | Source |
|---|---|
| Explainability expectation | Para. 50, derived from **Art. 2 RTS 6** (compliance staff must have "at least a general understanding of how the algorithmic trading systems and trading algorithms … operate" and be "in continuous contact" with technical staff): "As a result, on the one hand, the algorithmic trading systems and algorithms should be explainable and on the other hand, it is the investment firm's responsibility to ensure they can adequately explain how AI impacts their algorithms' decision-making." |
| Annual self-assessment | Para. 49, **Art. 9 RTS 6**: an annual self-assessment and validation process producing a validation report; NCAs should assess how firms account for AI use within it. Para. 51: conducted "on an article-by-article basis, covering all relevant articles of RTS 6". |
| Retraining is a material change | Paras. 30–31: a material change is "any modification that may alter the behaviour, risk profile, or compliance posture of an algorithm"; the listed change types include, under *Adaptive Capabilities*, "**Retraining or modifying machine learning components**". Firms "are required to timestamp, approve, and record all material changes." |
| Recalibration drift | Para. 30: firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested." |
| Governance framework | Para. 43, **Art. 1 RTS 6**: trading systems and algorithms are established and monitored "through a clear and formalised governance arrangement". |
| AI Act interaction | Para. 43: "The Artificial Intelligence (AI) Act (Regulation (EU) 2024/1689) defines what an AI system is… When an algorithmic trading system meets the definition of an AI system it will need to comply with the requirements in the AI Act." Whether a given EBM-driven system meets that definition is a legal determination, not one this skill makes. |

## Explainable Boosting Machines — technical facts

| Fact | Source |
|---|---|
| $g(E[y]) = \beta_0 + \sum_i f_i(x_i) + \sum_{(i,j)} f_{ij}(x_i,x_j)$; each $f_j$ "acts as a lookup table per feature, and returns a term contribution. These term contributions are simply added up." | InterpretML, *Explainable Boosting Machine* docs — <https://interpret.ml/docs/ebm.html> |
| Terms are fitted by bagging and cyclic gradient boosting "restricted to train on one feature at a time in round-robin fashion using a very low learning rate" | ibid. |
| GA²M — additive model plus a small number of pairwise interaction terms | Lou, Caruana, Gehrke & Hooker, "Accurate intelligible models with pairwise interactions", *KDD 2013*, pp. 623–631, doi:10.1145/2487575.2487579 |
| Reference implementation | Nori, Jenkins, Koch & Caruana, "InterpretML: A Unified Framework for Machine Learning Interpretability", arXiv:1909.09223 (19 Sep 2019) |
| Classification contributions are log-odds: "the y-axis values are in logits … because these graphs are in logarithm space, differences of +1 or +2 are quite significant" | InterpretML FAQ — <https://interpret.ml/docs/faq.html> |
| Monotonicity is applied either via the `monotone_constraints` constructor argument or post-fit via `monotonize` (isotonic regression). Post-processing is recommended, "as it prevents the model from compensating for the monotonicity constraints by learning non-monotonic effects in other highly-correlated features." | InterpretML FAQ; `ExplainableBoostingClassifier` API docs |
| **`monotonize` "only adjusts a single term and will not modify pairwise terms. When a feature needs to be globally monotonic, any pairwise terms that include the feature should be excluded from the model."** | `ExplainableBoostingClassifier.monotonize` — <https://interpret.ml/docs/python/api/ExplainableBoostingClassifier.html> |
| Fitted terms are stored as numpy arrays in `term_scores_`, so an EBM is directly editable | InterpretML FAQ |

The last row is the reason this engine treats a `GLOBAL` monotonicity claim on a feature
that also appears in an interaction term as a violation rather than a pass.

## What this engine does and does not certify

| Claim | Certified? |
|---|---|
| The reported contributions sum to the reported score | Yes — full precision, `math.fsum`, contributions recorded unrounded. |
| The record is reproducible from its own recorded inputs | Yes — every term is re-evaluated independently and compared term-by-term. |
| Declared monotonicity holds on the declared grid | Yes, within the grid and the stated scope. |
| The model is monotone in a feature outside the audit grid | **No.** The grid is the extent of the claim. |
| The model is globally monotone in a feature carrying an interaction term | **No** — reported as a violation. |
| The shape functions are correctly fitted, leak-free, or predictive | **No.** Out of scope; see `feature-engineering-without-leakage`, `walk-forward-validation-setup`, `point-in-time-database-for-ml-training-data`. |
| The shape tables have not been recalibrated | **No.** The fingerprint covers structure. Bind a calibration to a record with `shape_table_version`. |
| The entity is compliant with anything | **No.** This is an audit record, not a compliance opinion. |
