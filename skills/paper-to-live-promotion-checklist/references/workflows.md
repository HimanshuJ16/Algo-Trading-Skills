# Deep Workflow Reference — paper-to-live-promotion-checklist

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Multi-Criteria Gate Check Evaluation:**
   - Execute `PaperToLivePromotionGate.evaluate_gate()` verifying 6 quantitative criteria:
     - `min_paper_duration`: Minimum paper trading period ($\ge 20$ trading days).
     - `min_trades_count`: Minimum trade executions ($\ge 30$ trades).
     - `slippage_alignment`: Paper slippage vs modeled backtest slippage within tolerance ($\le 15\%$).
     - `accuracy_alignment`: Paper signal accuracy vs walk-forward out-of-sample accuracy.
     - `risk_controls_exercised`: At least 1 simulated or natural risk trigger logged.
     - `auth_reauth_survived`: At least 1 natural or forced token expiry cycle survived.

2. **Formal Human Sign-Off Document Generation:**
   - Produce a structured `PromotionDecisionReport` documenting date, reviewer ID, initial live position sizing multiplier ($0.25\times$ reduced live size), and rollback trigger rules.

3. **Reduced-Size Initial Live Deployment:**
   - Deploy live trading at a reduced capital fraction (e.g. 25% target position size) during the initial live review window.

4. **Post-Promotion Live Rollback Trigger Evaluation:**
   - Periodically execute `check_rollback_trigger()` during initial live trading.
   - Automatically revert to paper trading if live drawdown or slippage exceeds $2\times$ paper baseline.

## Failure Modes Observed in Production

- **Informal "Feels Ready" Promotion:** Flipper-switching strategies to live capital without quantitative evaluation of paper trading metrics.
- **Unexercised Risk Controls:** Promoting bots whose risk controls were never triggered or tested during paper trading.
- **Separate Paper Mode Code Base:** Running paper trading on a simplified separate code path, reintroducing train/serve skew when going live.
- **Full Capital Day-1 Allocation:** Skipping reduced initial live sizing, incurring heavy losses before discovering live execution quirks.

## Production Implementation Reference

- Reference code: `scripts/promotion_gate.py` (`PaperToLivePromotionGate`, `PromotionDecisionReport`, `GateCheckResult`).
- Automated unit tests: `scripts/test_promotion_gate.py`.
