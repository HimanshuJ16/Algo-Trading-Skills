# Framework & Serialization Coverage — offline-train-online-infer-deployment

| Framework / Serialization Standard | Relevance to this skill |
|---|---|
| scikit-learn | Source of the fitted parameters this skill exports. `StandardScaler` supplies `mean_`/`scale_`; binary `LogisticRegression` supplies `coef_` (shape `(1, n_features)`) and `intercept_`, with `predict_proba` defined as `expit(Xw + w₀)` (User Guide §1.1.11.1). |
| ONNX (Open Neural Network Exchange) | Cross-platform serialization format for models whose *structure* must survive the export — trees, ensembles, neural nets — which the JSON artifact here deliberately does not attempt. |
| XGBoost / LightGBM | Gradient-boosted tree models; use their native or ONNX export rather than this skill's linear artifact format, and apply only the digest/schema/parity hygiene. |
| PMML (Predictive Model Markup Language) | XML model exchange standard maintained by the Data Mining Group; an alternative to ONNX where an XML toolchain already exists. |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

**These are jurisdiction-specific and none of them is a general obligation on an algo
trading operation.** Check which, if any, applies to your entity and jurisdiction
before treating anything here as a requirement.

- **US banking supervision — model risk management.** The Federal Reserve, OCC and
  FDIC issued *Revised Guidance on Model Risk Management* on 17 April 2026 (Fed
  [SR 26-2](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm); OCC
  [Bulletin 2026-13](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html)),
  which **supersedes SR 11-7 (4 April 2011)** and rescinds OCC Bulletin 2011-12. It
  applies to supervised banking organizations — the Fed states it is most relevant to
  those with over $30 billion in total assets — and is *supervisory guidance*, not a
  rule. It does not bind a non-bank proprietary trading firm or fund. Cite it as a
  source of model-governance practice (validation, documentation, back-testing,
  ongoing monitoring), not as a compliance obligation, unless your entity is
  supervised. Material in this skill that maps to it: artifact versioning, digest
  verification, and the offline/online parity gate as ongoing-monitoring evidence.

- **India — SEBI AI/ML reporting.** SEBI circular
  [SEBI/HO/MIRSD/DOS2/CIR/P/2019/10](https://www.sebi.gov.in/legal/circulars/jan-2019/reporting-for-artificial-intelligence-ai-and-machine-learning-ml-applications-and-systems-offered-and-used-by-market-intermediaries_41546.html)
  (4 January 2019) requires SEBI-registered **market intermediaries** to report AI/ML
  applications and systems they offer or use; parallel circulars cover Market
  Infrastructure Institutions (Jan 2019) and Mutual Funds (May 2019). Scope is India
  and SEBI registration — it is not a disclosure obligation elsewhere. SEBI also
  published a *Consultation Paper on guidelines for responsible usage of AI/ML in
  Indian Securities Markets* (June 2025); as of this writing it is a consultation
  paper, and no finalised circular has been confirmed, so do not design to it as
  though it were in force.

- **Operational, not regulatory.** Artifact digests, schema pinning and parity gates
  are engineering controls this skill implements. Where a recordkeeping obligation
  does apply to your entity, the model id, version, digest and parity evidence are
  the artifacts an audit trail for algorithmic signal generation would draw on — see
  `backtest-audit-trail-for-regulatory-review` and `model-card-documentation-for-trading-models`.
