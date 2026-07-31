# Workflows for Post-Only Limit Repricing Under Fast Markets

1. **Spread Crossing Detection**:
   - Detect if proposed Post-Only limit price would cross current best bid/ask.
2. **Passive BBO Repricing**:
   - Reprice order to passive BBO boundary.
3. **Rejection Churn Audit**:
   - Audit reprice attempts to prevent exchange rate limit throttling.
4. **Audit Report Generation**:
   - Output structured fast market reprice report.