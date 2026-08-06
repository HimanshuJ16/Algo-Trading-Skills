# Workflows for Synthetic Labels from Triple Barrier Method

1. **Volatility Calculation**:
   - Calculate rolling log-return standard deviation as volatility scaling factor.
2. **Barrier Initialization**:
   - Establish upper take-profit, lower stop-loss, and vertical expiration barriers.
3. **Forward Horizon Path Search**:
   - Scan sub-period prices until first barrier contact.
4. **Dataset Construction**:
   - Attach synthetic labels ($+1, -1, 0$) and exit metadata to feature matrix for ML training.
