# Standards — execution-cost-model-recalibration-cadence

## Configuration defaults (calibrate before use)

These are the library's defaults, **not** industry standards. No regulator or standards
body publishes a mandatory tracking-error, bias, or sample-size limit for pre-trade
transaction cost models. The right values depend on how the cost estimate is consumed —
a model feeding portfolio construction tolerates less bias than one feeding a post-trade
scorecard — on the instrument's liquidity tier, and on the firm's own cost-estimation
tolerance. Calibrate each against your own realized-IS distribution and record the
rationale.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_tracking_error_rmse_bps` | $3.5$ bps | Dispersion gate. Trips when per-trade prediction error is too noisy to be useful, even if it averages out. |
| `max_systematic_bias_bps` | $1.5$ bps | Level gate. Trips on persistent under- or over-prediction, which biases every downstream cost estimate in the same direction. Compared as $\|\bar{\epsilon}\|$, so both signs trip it. |
| `min_recalibration_sample_size` | $50$ trades | Refit gate. Below this the engine reports the breach but withholds parameters. Two free coefficients need enough observations to separate a regime change from noise; the hard floor is 2 (the identifiability minimum), and 50 is a governance default, not a statistical derivation. |
| `min_design_conditioning` | $10^{-6}$ | Identifiability gate on $\det/(S_{11}S_{22}) = 1 - \cos^2\theta$ between the two regressor columns. $1.0$ is orthogonal, $0.0$ perfectly collinear. |
| `MAX_PLAUSIBLE_DAILY_VOL_PCT` | $200\%$/day | Coarse units guard. Catches gross unit errors (a bps figure or an annualized figure in the percent field). It cannot catch every unit mistake — a decimal fraction such as $0.015$ is a valid, if tiny, percent — so the units contract is enforced primarily by documentation and by the field's name. |

## Model facts (verified against the primary literature)

| Fact | Source |
|---|---|
| Square-root impact law $I(Q) = Y \cdot \sigma \cdot \sqrt{Q/V}$, with a dimensionless prefactor $Y$ of order $0.5\text{–}1.0$, empirically robust across equities, futures and options | Tóth, Lempérière, Deremble, de Lataillade, Kockelkoren & Bouchaud, *Anomalous price impact and the critical nature of liquidity in financial markets*, **Phys. Rev. X 1, 021006 (2011)** ([arXiv:1105.1694](https://arxiv.org/abs/1105.1694)) |
| Direct estimation of equity impact coefficients from a large brokered order sample, with coefficient dependence on volatility, ADV and turnover | Almgren, Thum, Hauptmann & Li, *Direct estimation of equity market impact*, **Risk 18(7):58–62 (2005)** |

**Dimensional consequence, and why it is enforced in code**: the law is homogeneous —
$\sigma$ must be expressed in the *same relative unit* as the impact it predicts. Because
this module works in bps, `volatility_daily_pct` is given in percent and converted with
$\times 100$. A decimal fraction supplied in that field shrinks the impact term by
$100\times$, which does not merely mis-scale $\gamma$: it pushes essentially all fitted
cost into the spread coefficient and destroys the separation between the two terms.

**Model scope**: the fitted form is a static two-term cost *estimate*. It is not the
Almgren–Chriss optimal-execution framework (which solves for a trade schedule under a
risk-aversion parameter), and nothing here produces or optimises a schedule.

## Regulatory touchpoints

Neither instrument below sets a numeric TCA accuracy threshold. They bear on the
*cadence* and *governance* of model review, not on what counts as an acceptable RMSE.

| Instrument | Jurisdiction & applicability | Status | Relevance |
|---|---|---|---|
| **RTS 6**, Commission Delegated Regulation (EU) 2017/589, **Art. 9 "Annual self-assessment and validation"** | EU; investment firms engaged in algorithmic trading under MiFID II Art. 17 | **Mandatory** | Requires an annual self-assessment and validation of the firm's algorithmic trading systems, strategies, governance and business continuity, producing a validation report drawn up by the risk management function, audited by internal audit where it exists, approved by senior management, with identified deficiencies remedied. Where a firm's execution cost model forms part of those systems, this sets an **annual floor** on review cadence — the trigger-based recalibration here operates *inside* that floor, not instead of it. Related articles in Chapter II: Art. 5 (general testing methodology), Art. 6 (conformance testing), Art. 7 (testing environments), Art. 8 (controlled deployment), Art. 10 (stress testing). |
| **SR 26-2** (Federal Reserve, 17 Apr 2026) / **OCC Bulletin 2026-13**, "Revised Guidance on Model Risk Management" | US; most relevant to banking organizations with over \$30 billion in total assets supervised by the Federal Reserve, and to OCC-supervised banks | **Supervisory guidance, not enforceable rules.** OCC Bulletin 2026-13 states the guidance does not set forth enforceable standards or prescriptive requirements and that non-compliance will not itself result in supervisory criticism. Supersedes SR 11-7 (2011) and SR 21-8; OCC Bulletin 2011-12 is rescinded. | Frames periodic model validation, ongoing monitoring and outcomes analysis as the governance pattern this skill implements. **Applicability caveat**: it addresses supervised banking organizations. A hedge fund, prop firm, or asset manager is not subject to it and should treat it as a reference practice, not an obligation. Cite the current instrument — SR 11-7 is no longer the operative guidance. |

Both entries were verified as current at the time of writing; model-risk and algorithmic
trading guidance is revised periodically, so re-verify status before relying on either in
a compliance filing.

## Known limitations of this engine

- **In-sample fit.** Refitted coefficients are scored on the trades that triggered the
  recalibration. Least squares minimises in-sample squared error by construction, so
  `post_refit_rmse_bps` is *guaranteed* no worse than the incumbent's — that is
  arithmetic, not evidence of improvement. Validate out-of-sample before promotion.
- **Trigger arm only.** The calendar arm of the cadence (weekly/monthly review, and the
  RTS 6 Art. 9 annual floor above) is a scheduler concern and is not modelled here. The
  engine also enforces no minimum interval between recalibrations.
- **No causal attribution.** The engine detects that the model no longer matches realized
  cost; it does not distinguish a genuine regime change from a venue outage, a
  fee-schedule change, or a few outlier parent orders.
- **Equal weighting.** Every trade contributes equally to the objective, so large orders —
  where the impact term matters most — do not dominate the fit.
- **Sample hygiene is the caller's responsibility.** The engine does not deduplicate,
  window, order, or verify that realized IS was measured after execution completed.
