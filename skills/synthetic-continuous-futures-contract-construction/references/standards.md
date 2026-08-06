# Standards for Synthetic Continuous Futures Contract Construction

| Parameter | Standard Choice |
|---|---|
| Primary Roll Trigger | `VOLUME_CROSSOVER` (roll when $V_{\text{next}} > V_{\text{front}}$). |
| Secondary Roll Trigger | `OPEN_INTEREST_CROSSOVER`. |
| Default Adjustment | `ADDITIVE_BACK_ADJUSTMENT` ($\text{Price}_{\text{adj}} = \text{Price}_{\text{raw}} - \text{CumGap}$). |
| Long-term Commodity Adjustment | `PROPORTIONAL_RATIO` (prevents negative prices). |
