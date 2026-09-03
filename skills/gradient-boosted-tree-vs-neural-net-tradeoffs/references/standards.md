# Standards — gradient-boosted-tree-vs-neural-net-tradeoffs

## Read this first

Three claims are commonly presented as "Engineering Standards" with the word **MUST**.
None of them is a standard:

| Claim | Why it is not a standard |
|---|---|
| "SR 11-7 Compliance \| Regulated signals MUST require SHAP / EBM feature explainability." | No regulator has published this requirement. SR 11-7 was also superseded on 17 April 2026. |
| "Tabular Data Preference \| Tabular financial datasets MUST default to GBDT baseline." | This is a well-evidenced *empirical prior*, not a standard anyone promulgated. Restated below as evidence. |
| "Sub-Millisecond Latency \| Sub-500 microsecond strategies MUST prefer GBDT C++ runtimes over PyTorch/GPU." | Inference latency is governed by model size, batch size and runtime, not by model family. A 5,000-tree LightGBM model is not faster than a two-layer MLP under ONNX Runtime. This has to be measured. |

None of the three should be cited as authority; what the sources actually say is set out
below. Two things to keep straight throughout:

1. **US model-risk guidance is not a rule**, and it addresses banking organizations.
2. **The EU explainability expectation is real but indirect**, and the document that
   articulates it is explicitly non-binding.

## United States — model risk management

**Current instrument:** Federal Reserve **SR 26-2**, *Revised Guidance on Model Risk
Management*, 17 April 2026, issued jointly by the Board of Governors, the OCC and the
FDIC. <https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm>

**Superseded:** the letter states it supersedes and replaces "SR letter 11-7, *Guidance
on Model Risk Management* (issued April 4, 2011)" and SR letter 21-8. **Any document in
this repository or elsewhere that cites SR 11-7 as the governing US model-risk standard
is out of date as of 17 April 2026.** Keep the citation only when describing a record
produced under the predecessor guidance.

| Point | What the source says (SR 26-2 attachment) |
|---|---|
| Legal status | "This guidance does not set forth enforceable standards or prescriptive requirements; accordingly, non-compliance with this guidance will not result in supervisory criticism against a banking organization." |
| Who it addresses | Banking organizations. "Models used by banking organizations with total assets of $30 billion or less typically are subject to internal risk management and governance practices appropriate for the size and risk profile of these banking organizations, and generally excluding them from this guidance is consistent with a tailored supervisory approach." It is **not** addressed to proprietary trading firms, hedge funds or non-bank broker-dealers. |
| What counts as a model | "a complex quantitative method, system, or approach that applies statistical, economic, or financial theories to process input data into quantitative estimates. The term 'model' … **excludes simple arithmetic calculations, such as those found within spreadsheets, as well as deterministic rule-based processes and software where there are no statistical, economic, or financial theories underpinning their design or use.**" |
| Are GBDTs / LSTMs in scope | Yes. Footnote 3: "Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance… However, the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models." |
| Where interpretability appears | Under *Components of Model Validation — Conceptual Soundness*: "While evaluating theoretical construction may be important for some models, other assessments—such as **interpretability measures or benchmarking to other models**—may be more practical for other models." Interpretability is offered as one practical assessment, not required, and it is offered *alongside* benchmarking. |
| Where the bake-off appears | "Testing may include a range of activities, from out-of-sample and out-of-time testing, to **a comparison of alternative assumptions and methodologies**, to a critical assessment of data quality, relevance, and inputs." |
| SHAP | Not mentioned anywhere in the guidance. No attribution method is named or required. |
| Documentation | "Adequate documentation helps to support effective model risk management." Permissive; no prescribed document set. |

**Consequence for this skill.** The selector engine is a deterministic weighted sum with
no statistical, economic or financial theory underpinning it, so it falls inside the
guidance's own *exclusion* — it is not itself a model under SR 26-2. The GBDT or neural
network it recommends is.

## European Union — algorithmic trading

**Instrument:** Commission Delegated Regulation (EU) **2017/589** (MiFID II RTS 6),
organisational requirements for investment firms engaged in algorithmic trading. This
*is* binding law, and it applies to investment firms — not to models as such.

**Interpretive source:** ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*,
**ESMA74-1505669079-10311**, February 2026.
<https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf>

Its own status, para. 3: "This supervisory briefing is issued under Article 29(2) of the
ESMA Regulation… **The content of this briefing is non-binding and not subject to a
'comply or explain' mechanism.**"

| Point | Source |
|---|---|
| Explainability expectation | Para. 50, derived from **Art. 2 RTS 6** (compliance staff must have "at least a general understanding of how the algorithmic trading systems and trading algorithms of the investment firm operate"): "As a result, on the one hand, the algorithmic trading systems and algorithms should be explainable and on the other hand, it is the investment firm's responsibility to ensure they can adequately explain how AI impacts their algorithms' decision-making." **No technique is named.** SHAP, EBMs, attention maps and written documentation are all candidate means; none is mandated. |
| **Changing model family is a material change** | Para. 31: "A material change or substantial update is any modification that may alter the behaviour, risk profile, or compliance posture of an algorithm, algorithmic trading system or algorithmic trading strategy. Investment firms are required to timestamp, approve, and record all material changes." The accompanying change-type table lists, under *Adaptive Capabilities*, "**Retraining or modifying machine learning components**". Swapping a GBDT for an LSTM in a live strategy is squarely inside this. |
| Retesting trigger | Para. 30: "Testing of an algorithm, algorithmic trading system or algorithmic trading strategy is required following each 'material change' or 'substantial update' thereof." Firms "should manage the risk that a series of minor or small changes due to recalibrations could accumulate over time … into a material change in the model output without it being tested." |
| Annual self-assessment | Para. 49, **Art. 9 RTS 6**: "NCAs should assess how firms are taking into consideration the use of AI as part of their self-assessment and validation under Article 9 of RTS 6." Para. 51: conducted "on an article-by-article basis, covering all relevant articles of RTS 6". |
| ML strategies are in scope of "algorithmic trading" | Para. 11 lists, under *Order Generation Logic*: "Signal-based trading, quantitative models, or machine learning-driven strategies including where the activities are not involved in direct execution but influence algorithmic trade decision-making and parameter determination." |
| AI Act interaction | Para. 43: "When an algorithmic trading system meets the definition of an AI system it will need to comply with the requirements in the AI Act" (Regulation (EU) 2024/1689). Whether a given GBDT or LSTM system meets that definition is a legal determination, not one this skill makes. |

## The model-family evidence

These are the sources behind each dimension prior in
`scripts/model_family_selector.py`. Each `DimensionPrior` carries its evidence string,
and the string is reproduced in every report's `dimension_evidence`.

| Dimension | Prior | Evidence |
|---|---|---|
| `tabular_data_fit` | GBDT 9.5 / NN 5.5 | Grinsztajn, Oyallon & Varoquaux, *Why do tree-based models still outperform deep learning on typical tabular data?*, NeurIPS 2022 Datasets & Benchmarks, arXiv:2207.08815 — across 45 datasets, "tree-based models remain state-of-the-art on medium-sized data (~10K samples)". Shwartz-Ziv & Armon, *Tabular Data: Deep Learning is Not All You Need*, Information Fusion 81 (2022) 84–90, arXiv:2106.03253 — "XGBoost outperforms these deep models across the datasets, including the datasets used in the papers that proposed the deep models", and "XGBoost requires much less tuning". |
| `sequential_pattern_extraction` | GBDT 3.0 / NN 9.5 | Both papers above scope their claims to *tabular* data; Grinsztajn et al. open by conceding deep learning "has enabled tremendous progress on text and image datasets". Raw tick and order-book sequences are that regime. |
| `interpretability_compliance` | GBDT 9.0 / NN 4.0 | Lundberg, Erion, Chen, DeGrave, Prutkin, Nair, Katz, Himmelfarb, Bansal & Lee, *Explainable AI for Trees: From Local Explanations to Global Understanding*, arXiv:1905.04610 (published as *From local explanations to global understanding with explainable AI for trees*, Nature Machine Intelligence 2, 56–67, 2020) — contributes "the first polynomial time algorithm to compute optimal explanations based on game theory" for tree ensembles. This is a **tractability** gap: exact attributions for trees versus sampling- or gradient-based estimates for deep networks. It is not a regulatory gap — see both sections above. |
| `inference_speed_latency` | GBDT 9.0 / NN 5.0 — **low confidence** | Directional only. Latency is set by model size, batch size and runtime, not by family. Measure it against a real budget; see `model-inference-latency-budget-for-live-trading`. |
| `regime_shift_robustness` | GBDT 6.0 / NN 6.0 — **deliberately neutral** | Gardner, Popović & Schmidt, *Benchmarking Distribution Shift in Tabular Data with TableShift*, NeurIPS 2023 Datasets & Benchmarks, arXiv:2312.07577 — across 15 shift tasks and 19 model types, "no model consistently outperforms the standard tabular baselines of XGBoost, LightGBM, or CatBoost"; no technique eliminates shift gaps, and robustness methods "tend to shrink gaps by reducing average ID performance, not by improving OOD performance"; ID and OOD accuracy correlate at ρ=0.81. Neither family has a defensible advantage, so the prior is equal — and an equally-scored dimension cannot tilt `score_gap`. Countervailing point on the GBDT side: scikit-learn documents that tree predictions "are neither smooth nor continuous, but piecewise constant approximations, and therefore they are not good at extrapolation" (<https://scikit-learn.org/stable/modules/tree.html>) — a feature moving outside its training range saturates a GBDT instead of extrapolating, which is exactly what a regime shift does. |

## What this engine does and does not certify

| Claim | Certified? |
|---|---|
| The published scores recompute from the record's own priors and weights | Yes, to the two decimal places the record publishes. |
| The recommendation follows from `score_gap` and `decision_margin` | Yes — both are in the record. |
| The recommended family will perform better on your data | **No.** This is a prior for a bake-off, never a substitute for one. |
| The recommended family will meet the stated latency budget | **No.** The latency dimension is a family-level prior, not a measurement. |
| Either family is robust to a regime shift | **No.** The evidence supports no such claim for either. |
| The entity is compliant with anything | **No.** No output of this engine is a compliance opinion. |
