# Attribution Standards — explainability-for-live-trading-signals

## Additive attribution methods

All three methods below produce an *additive* local explanation, i.e. one satisfying
the local-accuracy (efficiency) property $f(x) = \phi_0 + \sum_{i=1}^M \phi_i$
(Lundberg & Lee, *A Unified Approach to Interpreting Model Predictions*, NeurIPS 30,
2017). That identity is what this skill reconciles against the emitted score.

| Attribution Method | Additive form | Primary Use Case | Base value $\phi_0$ |
|---|---|---|---|
| TreeSHAP (`shap.TreeExplainer`) | $\hat{Y} = \phi_0 + \sum_i \phi_i$ | Tree ensembles (XGBoost / LightGBM / sklearn forests) | `explainer.expected_value` |
| Integrated Gradients (Captum) | Path integral of gradients from a baseline $x'$ | Neural networks | $f(x')$, the chosen baseline's output |
| Linear contributions | $\phi_i = w_i x_i$ | Linear / factor / CAPM-style models | Intercept $b$ |

Integrated Gradients is additive only up to a numerical-integration error that grows
with a coarse step count; reconcile it with a tolerance matched to `n_steps`, not with
the exact-explainer default.

## Output space — the reconciliation trap

The shap `TreeExplainer` documentation states the property as: *"the sum of the SHAP
values plus the `expected_value` equals the model's output (**in the specified output
space**)"*. That parenthesis is where production pipelines break.

| `model_output` | $\phi_i$ sum to | Notes |
|---|---|---|
| `"raw"` (default) | Raw margin — **log-odds** for XGBoost `binary:logistic` | Does **not** equal `predict_proba` |
| `"probability"` | The probability output | Only supported with `feature_perturbation="interventional"` |
| `"log_loss"` | The natural log of the model loss | Requires labels |

`feature_perturbation` also matters: `"interventional"` requires a background dataset
and handles feature dependence by causal-inference rules; `"tree_path_dependent"`
needs no background data and uses the training-example counts down each leaf. The two
yield different `expected_value`s **and** different $\phi_i$ for the same model, so
$\phi_0$ must come from the same explainer instance as the $\phi_i$.

**Reference tolerance.** shap's own additivity assertion is
$\max |{\sum\phi} - f(x)| / (|\sum\phi| + 10^{-2}) < 10^{-2}$ — roughly 1% relative,
deliberately loose to accommodate float32 tree ensembles. This skill defaults far
tighter ($10^{-6}$ absolute + $10^{-6}$ relative) because it is a compliance gate on
already-computed float64 attributions. Loosen it explicitly for float32 or GPU-trained
models; do not disable it.

Sources: Lundberg & Lee, NeurIPS 2017 (arXiv:1705.07874); Lundberg, Erion & Lee,
*From local explanations to global understanding with explainable AI for trees*,
*Nature Machine Intelligence* 2, 56–67 (2020); shap `TreeExplainer` API documentation,
<https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html>.

## Regulatory & Operational Notes

Scope these carefully. **No regulator listed below mandates per-signal feature
attribution.** What they mandate is that records exist and be sufficient for review;
attribution is a means of meeting that bar and of making an internal model-governance
process defensible, not a rule in itself.

| Framework | Issuer / jurisdiction | Status | Relevance |
|---|---|---|---|
| MiFID II Art. 17(2), Directive 2014/65/EU | EU (national competent authorities) | Mandatory for investment firms engaged in algorithmic trading in the EU | Requires the firm to keep records "sufficient to enable its competent authority to monitor compliance". A reconciled per-signal attribution record is one way to make an ML signal reviewable. Firms using a high-frequency algorithmic trading technique must additionally store accurate, time-sequenced records of orders, cancellations, executions and quotes. |
| RTS 6, Commission Delegated Regulation (EU) 2017/589 | EU | Mandatory, supplements MiFID II Art. 17 | Art. 5 requires documented development-and-testing methodologies covering design, performance, recordkeeping and approval before deployment or substantial update; Art. 9 requires an annual self-assessment and validation. Explanation records feed both. |
| RTS 25, Commission Delegated Regulation (EU) 2017/574 | EU | Mandatory | Business-clock synchronisation to UTC for reportable events. This skill stamps audit records in UTC so they align with order records kept under it — RTS 25 does not itself cover explanation logs. |
| SR 11-7 / OCC Bulletin 2011-12 | **US Federal Reserve and OCC** — *not the SEC* | Supervisory guidance (principles-based), binding only on Fed/OCC-supervised banking organisations | Model development, implementation, use, validation and governance across the model lifecycle. A non-bank proprietary trading firm or a registered investment adviser is **not** subject to it, though many adopt it voluntarily as a model-risk framework. |
| EU AI Act, Regulation (EU) 2024/1689 | EU | In force since 1 Aug 2024; Annex III high-risk obligations phased | **Securities trading models are not listed in Annex III.** The financial entries are point 5(b) (creditworthiness of natural persons, excluding fraud detection) and life/health insurance risk assessment and pricing. A proprietary trading signal model that makes no determination about a natural person is generally **not** a high-risk AI system, so Chapter III transparency obligations do not attach by default. Do not cite the AI Act as the reason to explain a trading signal without first checking whether the specific system touches an Annex III use case. |

Sources: Directive 2014/65/EU Art. 17; Commission Delegated Regulation (EU) 2017/589
(RTS 6) Arts. 5 and 9; Commission Delegated Regulation (EU) 2017/574 (RTS 25);
Federal Reserve Supervisory Letter SR 11-7, 4 April 2011,
<https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107.pdf>, and its companion
OCC Bulletin 2011-12; Regulation (EU) 2024/1689 Art. 6(2) and Annex III,
<https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng>.

Jurisdictional scope is not transferable: confirm which of the above actually binds
your entity before citing any of them in a governance document.
