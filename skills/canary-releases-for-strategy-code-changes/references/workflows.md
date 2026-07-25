# Workflows for Strategy Canary Releases

1. **Phase 1: Shadow Validation (Weeks 1-2)**
   - Set strategy to `DeploymentPhase.SHADOW`.
   - The strategy receives live market data and computes signals.
   - Signals are intercepted, logged to a database as "hypothetical fills," but never routed.
   - Analyze log data against target metrics (win rate, frequency).

2. **Phase 2: Canary Deployment (Weeks 3-4)**
   - Promote strategy to `DeploymentPhase.CANARY`.
   - Set `canary_scale_factor` to `0.05` (5%).
   - Orders are scaled down mathematically and routed live.
   - Monitor real-world slippage and FIX rejection rates with minimal capital at risk.

3. **Phase 3: Production**
   - Promote strategy to `DeploymentPhase.PRODUCTION`.
   - Orders are routed at 100% capacity.
   - The Kill-Switch logic remains active as a permanent guardrail.
