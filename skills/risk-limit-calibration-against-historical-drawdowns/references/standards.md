# Standards for Risk Limit Calibration Against Historical Drawdowns

**Read this first.** No rule surveyed below sets a maximum-drawdown, daily-loss or
position-scalar *number* for a trading firm. Every default in
`scripts/drawdown_limit_calibrator.py` — the `1.5x` stress buffer, the `5%` floor, the
`50%` cap, the `3x` VaR daily-loss multiple, the `20%` position-scalar threshold, the
`20`-day horizon — is **operational risk policy this skill sets for itself**. Do not
describe any of it to a regulator, an auditor or a user as a regulatory minimum. What
the rules below establish is the *obligation to calibrate*: limits derived from the
firm's own capital base and risk tolerance, reviewed on a stated cadence, calibrated
on a window that includes stress rather than only the recent calm.

## Engineering defaults used by this skill

| Parameter | Default (this skill's policy, not a mandate) |
|---|---|
| `min_observations` | `252` daily observations (one year). |
| `ABSOLUTE_MIN_OBSERVATIONS` | `126` (~6 months). Refused below this whatever the caller passes. |
| `target_confidence_pct` | `99.0`, one-tailed. Additionally refused if the window cannot contain a single loss at that confidence (`n * (1-q) >= 1`). |
| `stress_buffer_multiplier` | `1.5`. Values below `1.0` are refused. |
| `horizon_days` | `20` (~1 trading month) for the two horizon-scaled methods. |
| Drawdown limit floor / cap | `5%` / `50%`, applied to **every** method, with `floor_binding` / `cap_binding` reported. |
| `daily_loss_var_multiple` | `3.0` x historical VaR. |
| `position_scalar_threshold_pct` | `20%` observed drawdown. |
| EVT tail fraction / min exceedances | `10%` of the sample / `25` exceedances. |
| Unevaluable input (non-finite return, return `<= -1.0`, non-positive capital, zero tail loss, unfittable tail) | Raise a `CalibrationError` subclass. Never emit a limit. |

## Quantitative definitions used

| Quantity | Definition | Source |
|---|---|---|
| **Ulcer Index** | Square root of the mean of the squared percentage drawdowns from the running peak. Computed over the whole series here; charting packages commonly use a rolling 14-period lookback instead. | Devised by Peter Martin (1987), published in Martin & McCann, *The Investor's Guide to Fidelity Funds* (1989). Definition as described by StockCharts ChartSchool — https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ulcer-index |
| **Historical VaR / ES** | Order statistics: `k = ceil((1-q) * n)`; VaR is the `k`-th smallest return negated, ES the negated mean of the `k` smallest. ES >= VaR holds by construction. | Standard historical-simulation estimator; no distributional assumption. |
| **Parametric h-day loss quantile** | `loss_h = -h * mu + z_q * sigma * sqrt(h)` under IID normal returns. Drift aggregates linearly in `h`, volatility with `sqrt(h)`. | Direct consequence of summing `h` IID normal returns; scaling a one-day VaR (which embeds `-mu`) by `sqrt(h)` mis-scales the drift term. |
| **GPD, POT tail** | With threshold `u`, `N_u` exceedances of `n` observations, shape `xi`, scale `beta`: `VaR_q = u + (beta/xi) * (((n/N_u)(1-q))^(-xi) - 1)`, and `ES_q = VaR_q/(1-xi) + (beta - xi*u)/(1-xi)`, requiring `xi < 1` for a finite ES. | Peaks-over-threshold model on the Pickands–Balkema–de Haan theorem; VaR/ES estimator as in the McNeil–Frey line of work. Formulas as stated in the review of VaR/ES estimation at https://arxiv.org/html/2405.06798v1 (see "POT/GPD"), and the `ES(p) = VaR(p) + (sigma + gamma[VaR(p) - u])/(1 - gamma)` form, which is algebraically identical. |
| **GPD method-of-moments fit** | `xi = (1 - mean^2/var)/2`, `beta = mean * (1 + mean^2/var)/2` on the excesses. | Derived directly from the GPD moments `E[Y] = beta/(1-xi)` (finite for `xi < 1`) and `Var[Y] = beta^2/((1-xi)^2 (1-2xi))` (finite for `xi < 1/2`) — https://en.wikipedia.org/wiki/Generalized_Pareto_distribution |

**Known limitation of the fit, stated rather than hidden:** because both sample moments
are positive, `xi = (1 - mean^2/var)/2` is structurally bounded above by `0.5`. The
estimator therefore cannot represent an infinite-variance tail and *understates* one.
It is used because it is closed-form and independently verifiable; a maximum-likelihood
or probability-weighted-moments fit (Hosking & Wallis 1987, *Technometrics* 29(3)) is
the correct upgrade where the tail shape genuinely drives the limit. The module logs a
warning once `xi >= 0.25`, beyond which the method-of-moments estimator's own variance
is not finite.

## Regulatory shape of the control

| Jurisdiction / Framework | Binds | What it actually requires |
|---|---|---|
| **EU — MiFID II RTS 6, Art. 15(4)** (Comm. Del. Reg. (EU) 2017/589) | Investment firms engaged in algorithmic trading | An investment firm "shall set market and credit risk limits that are based on its capital base, its clearing arrangements, its trading strategy, its risk tolerance", adjusted to account for the changing impact of orders on the market. This is the regulatory obligation to *calibrate* — the firm's own capital base and risk tolerance are the inputs, and **no percentage is specified**. |
| **EU — MiFID II RTS 6, Art. 9** | Same | An annual self-assessment and validation process, with a validation report covering the algorithmic trading systems, governance and approval framework, and business continuity arrangements. This is the closest thing to a mandated *recalibration cadence*: annual is the floor, not a ceiling, and the regulation does not prescribe when a limit must be re-derived. |
| **EU — MiFID II RTS 6, Art. 10** | Same | Stress testing as part of the Art. 9 self-assessment: the systems and controls must withstand increased order flows and market stresses, tested at high messaging and trade volumes (the previous six months' peak multiplied by two). Stress testing sits alongside limit calibration; it is not a substitute for it. |
| **Basel Framework — MAR33** (BCBS, version effective 1 Jan 2023) | **Banks** using the internal models approach for market risk capital — **not** a proprietary trading firm, fund or individual | Cited here only as supervisory practice for the *shape* of a tail calibration, never as an obligation on this skill's users. MAR33.2: "In calculating ES, a bank must use a 97.5th percentile, one-tailed confidence level" — supervisors moved from 99% VaR to 97.5% ES precisely because the tail average is the more informative statistic. MAR33.5, Table 1: liquidity horizons of 10, 20, 40, 60 and 120 days, scaled from a 10-day base horizon (MAR33.4) — a horizon is always stated explicitly, never implied. MAR33.6: the stressed-ES observation horizon "must, at a minimum, span back to and include 2007", updated at least quarterly. MAR33.7: current-observation data sets updated "no less frequently than once every three months". MAR33.8(2): a supervisor may require a shorter observation period during a volatility upsurge, but "the period should be no shorter than six months" — the anchor for this module's 126-observation hard floor. |
| **US — SEC Rule 15c3-5** (17 CFR 240.15c3-5) | A **broker or dealer with market access** — *not* the end trading firm | (c)(1)(i) requires controls reasonably designed to prevent orders that exceed "appropriate pre-set credit or capital thresholds in the aggregate"; (e)(1) requires an annual review of their effectiveness. The rule requires thresholds to exist and be reviewed; it sets none of their values, and the obligation sits with the broker-dealer. |

## Applicability caveats

- **Basel is not your rulebook unless you are a bank.** MAR33 binds banks calculating
  market risk capital under the internal models approach. Nothing in it obliges a
  proprietary trading firm, a fund or an individual to use 97.5% ES, a six-month
  minimum window, or a quarterly refresh. It is quoted above as evidence of what
  supervisors consider defensible practice for tail calibration, nothing more.
- **UK:** RTS 6 was assimilated into UK law post-Brexit and is reproduced in the FCA
  Handbook Technical Standards with unchanged article numbering; confirm the current
  UK text separately rather than assuming indefinite EU/UK parity.
- **Not verified here:** nothing in this file establishes a required drawdown
  percentage, a required stress-buffer multiplier, a required recalibration frequency
  for a non-bank, or that any particular tail estimator must be used. Those are
  engineering and risk-policy choices.

## Sources

| Claim | Source |
|---|---|
| RTS 6 Art. 15(4) risk limits set from the firm's own capital base, clearing arrangements, strategy and risk tolerance; Art. 9 annual self-assessment and validation report; Art. 10 stress testing within that self-assessment | Commission Delegated Regulation (EU) 2017/589 (RTS 6) — https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589 |
| MAR33.2 97.5th-percentile one-tailed ES; MAR33.4 10-day base liquidity horizon; MAR33.5 Table 1 liquidity horizons 10/20/40/60/120; MAR33.6 stressed observation horizon spanning back to and including 2007, updated at least quarterly; MAR33.7 current data sets updated at least every three months; MAR33.8(2) shorter supervisory period "no shorter than six months" | Basel Committee on Banking Supervision, Basel Framework MAR33 "Internal models approach: capital requirements calculation", version effective 1 Jan 2023 — https://www.bis.org/basel_framework/chapter/MAR/33.htm |
| Rule 15c3-5 applies to a broker or dealer with market access; aggregate pre-set credit/capital thresholds; annual review of effectiveness | 17 CFR 240.15c3-5 — https://www.law.cornell.edu/cfr/text/17/240.15c3-5 |
| Ulcer Index as the square root of the mean of squared percentage drawdowns; attribution to Peter Martin (1987) and Martin & McCann (1989) | StockCharts ChartSchool — https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ulcer-index |
| POT/GPD VaR and ES estimators and the `xi < 1` finiteness condition | "Estimating Value at Risk and Expected Shortfall: A Brief Review and Some New Developments" — https://arxiv.org/html/2405.06798v1 |
| GPD CDF, mean `beta/(1-xi)` (`xi < 1`) and variance `beta^2/((1-xi)^2(1-2xi))` (`xi < 1/2`), from which the method-of-moments estimator is derived | https://en.wikipedia.org/wiki/Generalized_Pareto_distribution |

Calibration is not enforcement. Where a calibrated number becomes a live limit, the
control that enforces it must be independent of strategy logic
(`kill-switch-and-drawdown-circuit-breakers`,
`portfolio-level-stop-loss-independent-of-strategy-stops`).
