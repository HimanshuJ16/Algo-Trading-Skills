# Workflows for Custodial vs Non-Custodial Trade-Off Assessment

## 1. Requirements Audit

- Collect the latency budget, monthly volume, gas sensitivity, counterparty tolerance,
  key-sovereignty mandate, and any regulatory constraint.
- `StrategyRequirements` validates on construction: NaN, infinite, negative and
  out-of-range values raise. A NaN latency budget is the dangerous one — every `<=`
  comparison against NaN is False, so it would skip every latency constraint and
  produce a confident recommendation from garbage.
- `max_counterparty_risk_pct` is a **tolerance budget** in percent of deployed
  capital. `0.0` means zero tolerance and excludes every exposed architecture.

## 2. Architecture Profiles

- Each candidate declares: `key_control`, `min_achievable_latency_ms` (a floor, not a
  typical value), `residual_counterparty_exposure_pct`, `gas_cost_burden`, and
  `provides_qualified_custodian`.
- `DEFAULT_ARCHITECTURE_PROFILES` is marked `is_illustrative_default=True`. The
  structural facts behind the defaults are cited in `references/standards.md`; the
  exposure and cost figures are **placeholders**. Replace them with figures from your
  own counterparty due diligence and set the flag to `False`.
- Profiles are copied at construction, so mutating your profile objects afterwards
  cannot retroactively change the constraints a past assessment was made under.

## 3. Hard Constraints (run before any scoring)

Any single failure disqualifies the architecture, with the reason recorded:

1. **Key sovereignty** — satisfied only by `KEY_CONTROL_SELF`. MPC co-control with a
   venue and third-party qualified custody are not sole control. A firm that accepts
   those should set `requires_key_sovereignty=False` and constrain via exposure instead.
2. **Counterparty exposure** — `residual > tolerance` disqualifies. Exposure exactly at
   the mandate limit passes.
3. **Latency floor** — `floor > budget` disqualifies. For on-chain execution the floor
   is block inclusion (Ethereum L1: 12,000 ms), not network round-trip.
4. **Qualified custodian** — required architectures without one are excluded when
   `regulatory_constraint=QUALIFIED_CUSTODIAN_REQUIRED`.

## 4. Composite Ranking (survivors only)

- Score $= w_{\text{lat}} \cdot \text{LatencyHeadroom} + w_{\text{cust}} \cdot \text{CustodyControl} + w_{\text{cost}} \cdot \text{CostEfficiency}$,
  default weights $0.40 / 0.35 / 0.25$, validated to sum to 1.0 and returned on the report.
- $\text{LatencyHeadroom} = 100 \cdot \left(1 - \frac{\text{floor}}{\text{budget}}\right)$, clamped to $[0, 100]$ —
  measures margin, so an architecture sitting exactly on the budget scores 0.
- $\text{CustodyControl} = 100 - \text{residual exposure pct}$.
- $\text{CostEfficiency} = 100 - \text{gas sensitivity} \times \text{gas cost burden}$, clamped to $[0, 100]$.
- The composite is **ordinal**: it orders survivors under weights you declared. It is
  not calibrated and small differences are not meaningful.
- Ties break on lower residual counterparty exposure, then architecture name.

## 5. Output

- If nothing survives, `NoEligibleArchitectureError` is raised with every reason. Do
  not catch it and fall back to the highest-scoring disqualified option — that is a
  mandate breach presented as a recommendation.
- The report carries the eligible ranking, the disqualified list with reasons, the
  weights applied, mitigations for the recommended architecture, and advisories:
  off-exchange settlement review above the volume threshold, a single-eligible-candidate
  warning, and an illustrative-defaults warning.
