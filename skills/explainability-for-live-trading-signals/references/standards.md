# Financial ML Standards — explainability-for-live-trading-signals

| Attribution Method | Standard Formula | Primary Use Case |
|---|---|---|
| TreeSHAP / SHAP | $\hat{Y} = E[Y] + \sum_{i=1}^M \phi_i$ | Non-linear tree ensemble attribution (XGBoost / LightGBM) |
| Integrated Gradients | Path integral of gradients | Deep learning & neural network feature attribution |
| Linear Contributions | $\phi_i = w_i \cdot x_i$ | Linear regression / CAPM / factor model attribution |

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with EU AI Act High-Risk AI transparency requirements, SEC model risk governance (SR 11-7), and institutional trading compliance audit trails.
