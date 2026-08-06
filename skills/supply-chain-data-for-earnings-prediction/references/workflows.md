# Workflows for Supply Chain Data for Earnings Prediction

1. **Supply Chain Graph Construction**:
   - Map key suppliers and customers with revenue dependency weights.
2. **Lead-Lag Feature Generation**:
   - Lag supplier revenue growth by 1-3 months ($t-1$ to $t-3$).
3. **Implied Growth Calculation**:
   - Combine supplier revenue lead and customer inventory drag.
4. **Surprise Z-Score Signal Generation**:
   - Compare implied growth against consensus estimates; emit directional signals.