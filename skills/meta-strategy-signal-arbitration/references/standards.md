# Standards for Meta-Strategy Signal Arbitration

| Metric | Engineering Standard |
|---|---|
| Risk Veto Precedence | Risk-off stop loss signals MUST take absolute precedence over alpha signals. |
| Internal Netting | Opposing sub-strategy orders for the same symbol MUST be netted prior to market routing. |
| Deadband Suppression | Signal changes below the deadband threshold ($\epsilon_{\text{deadband}}$) MUST NOT trigger rebalancing. |