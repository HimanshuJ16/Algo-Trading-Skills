# Deep Workflow Reference — reinforcement-learning-safety-constraints-for-execution

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Intercept Policy Action Proposal:**
   - Intercept raw action $\Delta Q_{\text{proposed}}$ output by RL policy network.

2. **Apply Safety Shields:**
   - **Spread Veto Guard:** If $\text{Spread} > \text{MaxSpread}$, force action to $0.0$.
   - **Terminal Inventory Horizon:** If remaining time $T \le T_{\text{horizon}}$, force liquidation action.
   - **Max Order Size:** Clip $|\Delta Q| \le \text{MaxOrderSize}$.
   - **Position Limit Cap:** Clip $|Q_{\text{current}} + \Delta Q| \le Q_{\text{max}}$.

3. **Shape Penalty Reward:**
   - Apply penalty to base reward: $R_{\text{safe}} = R_{\text{pnl}} - \lambda \cdot \mathbb{I}(\text{Intercepted})$.

4. **Log Action Interceptions:**
   - Maintain audit metrics for interception count and override ratio.

## Failure Modes Observed in Production

- **Unshielded Execution:** Transmitting unconstrained RL neural network actions directly to exchange API endpoints.
- **Overnight Inventory Leakage:** Failing to force inventory clearance prior to exchange session close.

## Production Implementation Reference

- Reference code: `scripts/rl_safety_guard.py` (`SafeRLExecutionGuard`, `ExecutionState`, `SafeAction`).
- Automated unit tests: `scripts/test_rl_safety_guard.py`.
