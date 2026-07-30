# Workflows for Currency Pair Quoting Convention Normalization

1. **Hierarchy Evaluation**:
   - Priority order: `EUR` > `GBP` > `AUD` > `NZD` > `USD` > `CAD` > `CHF` > `JPY`.
2. **Inversion Logic**:
   - If `CUR1/CUR2` violates priority $\implies$ Flip to `CUR2/CUR1`.
   - $\text{Bid}_{\text{std}} = 1 / \text{Ask}_{\text{inv}}$, $\text{Ask}_{\text{std}} = 1 / \text{Bid}_{\text{inv}}$.
3. **Pip Calculation**:
   - Pip Size $= 0.01$ for JPY terms, $0.0001$ for all other currencies.
   - $\text{Spread Pips} = \frac{\text{Ask}_{\text{std}} - \text{Bid}_{\text{std}}}{\text{Pip Size}}$.
