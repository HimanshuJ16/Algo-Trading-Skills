# Workflows for Research Idea Pipeline Tracking and Prioritization

1. **Idea Registration**:
   - Log candidate alpha idea with Sharpe expectation, capacity estimate, complexity, and data cost.
2. **Priority Score Calculation**:
   - Compute multi-factor priority score: $\frac{\text{Sharpe} \times \log_{10}(\text{Capacity})}{\text{Complexity} \times \text{DataCost}}$.
3. **Lifecycle Stage Management**:
   - Advance idea through PROPOSED -> BACKTESTING -> PAPER_TRADING -> PRODUCTION_READY (or REJECTED).
4. **Pipeline Ranking & Report Generation**:
   - Output structured pipeline report with top-ranked research projects.