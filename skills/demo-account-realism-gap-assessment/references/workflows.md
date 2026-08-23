# Deep Workflow Reference — demo-account-realism-gap-assessment

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Log Execution Metric Capture**:
   - Record environment label, symbol, side (BUY/SELL), arrival price, fill price,
     submission timestamp, fill timestamp, requested quantity, and filled quantity for
     both demo and live executions.
   - Side is mandatory: execution cost is only interpretable when signed relative to
     trade direction.

2. **Fail-Closed Validation**:
   - Reject transposed demo/live lists via the per-record `environment` label.
   - Reject non-finite or non-positive prices, non-positive requested quantity,
     `filled_qty` outside `[0, requested_qty]`, a `fill_time` before `submission_time`,
     an invalid side, and booleans supplied in numeric fields.
   - Rationale: in the pre-audit implementation every one of these conditions produced a
     *higher* realism score. A control that gates live capital must degrade toward
     caution, never toward optimism.

3. **Compute Latency & Slippage Discrepancies**:
   - Compute mean latency (ms) and mean **signed** slippage (bps) across demo vs live
     datasets. Positive slippage is adverse; negative is price improvement.
   - A mean live latency of zero is rejected rather than scored — live fills are not
     instantaneous, so this indicates broken timestamps.

4. **Calculate Realism Score ($R \in [0, 1]$)**:
   - Combine latency ratio (30%), exponential decay slippage penalty (40%), and fill
     rate ratio (30%).
   - The slippage gap is floored at zero, so a demo that is more adverse than live
     (a conservative simulation) is not penalised.

5. **Assess Sample Sufficiency**:
   - Compare `n_demo` and `n_live` against `min_samples`. A flagged result is indicative
     only and must not gate a capital allocation on its own.

6. **Apply the Sharpe Discount**:
   - Multiply a **positive** demo Sharpe by $R$ before capital allocation sign-off.
   - Return a non-positive demo Sharpe unchanged, with a warning; the discount cannot
     rehabilitate a strategy that already loses money on paper.
   - Compare $R$ against `promotion_threshold` for the sign-off decision.

## Production Implementation Reference

- Reference code: `scripts/realism_assessor.py` (`DemoRealismAssessor`, `ExecutionLog`, `RealismAssessmentResult`).
- Automated unit tests: `scripts/test_realism_assessor.py`.
