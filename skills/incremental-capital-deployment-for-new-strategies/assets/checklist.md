# Pre-Flight Checklist

## Input conventions (each one silently inverts the safety logic if wrong)

- [ ] Is `realized_max_drawdown_pct` a **positive magnitude in percent** (`4.5`, not `-4.5`)?
      A signed drawdown passes every promotion gate and fails the emergency gate.
- [ ] Is drawdown measured over the **current tier's window only**, matching `days_in_tier` —
      not since inception? A since-inception maximum ratchets and can never be cleared.
- [ ] Are `realized_sharpe` and `slippage_vs_backtest_ratio` measured on **live fills** at
      Tier 1+, not paper fills?
- [ ] Does the caller **persist `next_days_in_tier`** from the report, resetting the counter
      to 0 on every tier change?
- [ ] Are non-finite values (`NaN`/`Inf`) rejected upstream rather than passed through?

## Policy configuration

- [ ] Is strategy initialized at Tier 0 (Paper Trading, 0% capital)?
- [ ] Are promotion gates (minimum trading days, realized Sharpe, max drawdown, slippage,
      execution crashes) configured and reviewed against this firm's risk appetite rather
      than accepted as defaults?
- [ ] Are maintenance limits configured **strictly below** the emergency limit, so the
      one-tier step-down is reachable?
- [ ] Is the hysteresis band between each entry gate and the maintenance limit above it
      wide enough that a strategy will not oscillate across the boundary it just cleared?
- [ ] Is emergency deactivation active if realized max drawdown $\ge 12\%$?

## Interpretation

- [ ] Has `sharpe_gate_conclusive` been checked before describing a promotion as
      "validated"? At 30 days the Sharpe standard error (~2.90) exceeds the 1.0 threshold.
- [ ] Is live execution slippage audited prior to tier promotion, including at the
      50% $\to$ 100% step?
- [ ] When promotion was blocked, has `failed_gates` been read to see *which* condition
      failed, not just that one did?

## Downstream wiring

- [ ] Is a **pre-trade control** enforcing the allocated entitlement? This engine produces
      a number; it does not stop a strategy trading beyond it
      (SEC Rule 15c3-5(c)(1)(i) for US broker-dealers).
- [ ] Is `EMERGENCY_DEACTIVATED` wired to something that actually halts trading and
      flattens positions? A $0 entitlement is not a kill switch.
- [ ] Is each tier change timestamped, approved and recorded as a discrete material
      change, rather than logged once for "the rollout"?
- [ ] For EU/UK firms: has the person designated by senior management authorised the tier
      change before the new allocation is applied (MiFID II RTS 6 Art. 5)?
