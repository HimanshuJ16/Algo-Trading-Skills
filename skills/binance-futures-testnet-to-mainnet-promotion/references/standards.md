# Institutional Quantitative Standards

## 1. Out-of-Sample Testing
Ensure the strategy has been tested on unseen data to avoid curve-fitting and overfitting.

## 2. Risk Control Mechanisms
- **Position Sizing**: Limit capital at risk per trade (e.g., maximum 2%).
- **Hard Leverage Limits**: Cap maximum leverage strictly (e.g., 5x).
- **Hard Stop-Loss**: Require an exchange-level hard stop-loss for every deployed strategy to protect against local connectivity loss.

## 3. Operational Resilience
- Always use encrypted (HTTPS) endpoints.
- Validate API connections aggressively before transitioning states.
- Maintain separate API credentials for Testnet vs Mainnet.
