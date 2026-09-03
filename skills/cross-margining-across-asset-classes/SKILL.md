---
name: cross-margining-across-asset-classes
description: Quantitative multi-asset treasury module that estimates cross-margining
  offsets across clearing houses (CME, OCC, FICC), the initial-margin reduction they
  imply, and the resulting capital efficiency — for planning, not as a substitute
  for the clearing house's own margin calculation.
domain: Treasury & Clearing Operations
subdomain: Portfolio & Cross Margining
tags:
- cross-margining
- portfolio-margin
- cme
- occ
- ficc
- margin-offset
- capital-efficiency
brokers_frameworks:
- CME SPAN 2
- OCC STANS
- ISDA SIMM (aggregation methodology)
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-asset trading firms, market makers, and treasury desks holding correlated positions across different clearing houses or asset classes (e.g. S&P 500 futures `ES` at CME vs. S&P 500 options `SPX` at OCC vs. ETF `SPY`; or Treasury futures `ZN` at CME vs. cash Treasuries cleared at FICC/GSD). Calculating standalone margin for each asset class in isolation traps idle collateral. This module estimates the cross-margin offset, quantifies the dollar margin saving, and reports the capital efficiency gain, with an audit trail of which offsets were applied.

The aggregation used is the ISDA SIMM cross-risk-class shape, $\sqrt{\sum_r IM_r^2 + \sum_{r \neq s} \psi_{rs} IM_r IM_s}$, applied across asset classes rather than SIMM risk classes.

## When NOT to Use

- **As the clearing house's margin number.** CME SPAN 2 derives margin from historical-VaR plus stress scenario revaluation across thousands of scenarios; OCC STANS uses full-portfolio Monte Carlo at a 99% Expected Shortfall measure. Neither aggregates standalone margins by a pairwise correlation. This module gives a planning estimate; reconcile against the CCP or clearing broker figure before committing collateral or sizing positions against released capital.
- **When no cross-margin arrangement covers the pair.** Offsets are only realizable inside an active program with the required account structure. Without one the correct estimate is the standalone sum, which is what the module returns by default.
- **For intraday collateral sufficiency or margin-call decisions.** The estimate is static and correlation-based; use the live broker/CCP requirement and `margin-utilization-circuit-breaker` for those.
- **As a stress or liquidation-risk measure.** Offsets calibrated on normal-market correlations overstate protection precisely when correlations converge in a crisis.

## Prerequisites

- Position inventory with standalone initial margin requirements ($M_i$) per asset class, one aggregated figure per asset class (duplicated identifiers are rejected).
- Pairwise correlation / offset credits ($\rho_{i,j}$) sourced from the governing clearing arrangement, not estimated from returns, and recorded with the program that grants them.
- **Account eligibility actually in place.** For the CME-FICC/GSD arrangement, participation requires the same dually-registered FCM (CFTC) and broker-dealer (SEC) at both clearing houses, and a signed cross-margin participant agreement between the BD-FCM, CME Group and FICC. An unregistered account earns no offset regardless of how correlated the positions are.

## Workflow

1. **Standalone Margin Summation**:
   - Compute total un-offset margin: $M_{\text{standalone}} = \sum_i M_i$.
   - Reject non-finite, negative, or duplicated components before aggregating — a duplicated asset-class identifier would be looked up as a self-pair and silently mispriced.
2. **Register Only Contractually Granted Offsets**:
   - Register $\rho_{i,j}$ per pair, tagged with the program (`CME-OCC`, `CME-FICC/GSD`). Any pair left unregistered receives `default_correlation` — 1.0, i.e. **no offset** — so the estimate degrades toward the standalone sum rather than inventing a benefit. `default_correlation=0.0` is a deliberate, not a default, choice.
3. **Cross-Margined Risk Reduction**:
   - Compute netted portfolio margin:
     $$M_{\text{cross}} = \sqrt{\sum_i M_i^2 + 2 \sum_{i < j} \rho_{i,j} M_i M_j}$$
   - If the radicand is materially negative, the pairwise offsets are jointly impossible (not positive semi-definite) — raise, do not clamp to zero. Three legs pairwise at $\rho = -0.9$ is arithmetically inconsistent, and clamping it would report a 100% margin saving.
   - Apply the model-risk floor $M_{\text{floor}} = f \times M_{\text{standalone}}$ (default $f = 0.20$). This is an **internal prudential parameter, not a clearing house or regulatory rule** — see Standards.
4. **Capital Savings & Efficiency Calculation**:
   - $\text{Margin Savings USD} = M_{\text{standalone}} - M_{\text{cross}}$.
   - $\text{Capital Efficiency Gain Pct} = \frac{\text{Margin Savings}}{M_{\text{standalone}}} \times 100\%$.
5. **Reconcile, Then Re-allocate**: compare the estimate against the CCP/clearing-broker requirement, and release freed collateral to the capital pool only up to the reconciled figure.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the Estimate as the Clearing House Number**: sizing positions against a correlation-aggregated estimate when the CCP computes margin by scenario revaluation (SPAN 2 HVaR + stress) or Monte Carlo Expected Shortfall (STANS). The two will not agree; the difference is a real collateral shortfall.
- **Defaulting Unregistered Pairs to $\rho = 0$**: a missing offset entry silently grants $\sqrt{M_1^2 + M_2^2}$ instead of $M_1 + M_2$ — a fabricated diversification benefit for a pair with no arrangement behind it. Fail closed at $\rho = 1$ and log the unregistered pairs.
- **Assuming 100% Margin Offset**: assuming perfectly negatively correlated positions (e.g. long futures vs. short stock) eliminate 100% of margin, ignoring clearing house model conservatism and short-option/per-contract minimums.
- **Clamping a Negative Radicand to Zero**: with three or more asset classes, individually plausible pairwise offsets can be jointly inconsistent. `max(0, variance)` turns that inconsistency into a near-zero margin requirement instead of an error.
- **Unregistered Cross-Margin Accounts**: computing savings without the dually-registered FCM/BD and signed participant agreement at CME-OCC / CME-FICC. The offset is an account-structure entitlement, not a property of the positions.
- **Ignoring Correlation Breakdown in Stress Tests**: relying on normal-market correlation offsets during liquidity crises when correlations converge toward 1 and the offset evaporates.
- **Treating the 20% Floor as a Rule**: it is a configurable model-risk guard with no CCP or regulator behind it; citing it to a risk committee as a clearing requirement is a compliance misstatement.

## Verification

- Instantiate `CrossMarginingCalculator(minimum_floor_pct=0.20)`. Register $\rho = -0.80$ for `EQUITY_FUTURES`/`INDEX_OPTIONS`, then input $M_{\text{EquityFutures}} = \$500{,}000$ and $M_{\text{IndexOptions}} = \$400{,}000$. Since $500{,}000^2 + 400{,}000^2 + 2(-0.80)(500{,}000)(400{,}000) = 90 \times 10^9$, verify $M_{\text{cross}} = \$300{,}000$ exactly, savings $\$600{,}000$, and capital efficiency gain $66.67\%$, with `is_floor_applied` false.
- Verify that omitting the offset registration returns $\$900{,}000$ (no saving) and populates `unregistered_pairs` — the fail-closed path.
- Verify three equal legs registered pairwise at $\rho = -0.9$ raise `InconsistentCorrelationError`, while $\rho = -0.5$ (the PSD boundary) returns $0$ and is accepted.
- Run `python -m unittest discover -s skills/cross-margining-across-asset-classes/scripts` and confirm a 100% pass rate.

## Related Skills

- `capital-efficiency-across-cross-margined-strategies`
- `broker-account-margin-call-handling`
- `multi-leg-strategy-margin-optimization`
- `options-margin-span-calculation-global`
- `margin-utilization-circuit-breaker`
