# Workflows for Corporate Action Adjustments

1. **Event Registration**:
   - Record events: `date`, `event_type` ('SPLIT', 'DIVIDEND'), `value` (e.g. 2.0 for 2-for-1 split, or $1.50 for dividend).
2. **CAF Computation**:
   - For each date $t$: $\text{CAF}_t = \prod_{\tau > t} \alpha_\tau$.
3. **Data Series Transformation**:
   - $P_{adj}(t) = P_{raw}(t) \times \text{CAF}_t$.
   - $V_{adj}(t) = V_{raw}(t) / \text{CAF}_t$.
4. **Execution Protocol**:
   - Compute signals using $P_{adj}$.
   - Execute trade orders using $P_{raw}(t)$ and credit cash for dividends on ex-date: $\text{Cash} += \text{Positions} \times D$.
