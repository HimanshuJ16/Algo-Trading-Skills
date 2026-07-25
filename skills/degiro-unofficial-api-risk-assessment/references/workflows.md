# Deep Workflow Reference — degiro-unofficial-api-risk-assessment

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Session Login & Token Extraction**:
   - Issue POST `https://trader.degiro.nl/login/secure/login`.
   - Store returned `sessionId`, `intAccount`, and client ID.

2. **Continuous Risk Evaluation**:
   - Monitor login attempt frequency, session age, and payload integrity.
   - Calculate composite `RiskScore` (0.0 to 1.0).

3. **Pre-Trade Dry-Run (`checkOrder`)**:
   - Issue POST `https://trader.degiro.nl/trading/secure/v5/checkOrder`.
   - Verify transaction fee estimation and confirmation ID.

4. **Order Execution & Circuit Breaker**:
   - Dispatch order via `/trading/secure/v5/order` only if `checkOrder` passes and `RiskScore <= 0.70`.

## Production Implementation Reference

- Reference code: `scripts/degiro_client.py` (`DEGIROUnofficialRiskManager`, `RiskEvaluation`).
- Automated unit tests: `scripts/test_degiro_client.py`.
