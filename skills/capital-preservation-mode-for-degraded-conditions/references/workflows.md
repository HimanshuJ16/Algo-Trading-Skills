# Workflows for Capital Preservation

1. **Out-of-Band Integration**: Deploy the `CapitalPreservationEngine` as a middleware layer in the Order Management System (OMS).
2. **Threshold Calibration**:
   - `max_daily_drawdown`: Set to the firm's absolute risk tolerance per desk.
   - `max_orders_per_minute`: Set to 2x the normal peak operating frequency.
   - `max_consecutive_errors`: Set to 5 (indicates a disconnected session or broken venue).
3. **Trigger Event**: Upon returning `False` from the pre-trade check:
   - The OMS drops the new order.
   - The OMS immediately fires a high-priority alert (e.g., PagerDuty) to the quant team.
   - Optional: The OMS routes "Flatten" orders to close existing open positions passively.
4. **Manual Reset**: The engine requires a deliberate, manual API call (`reset_engine(authorization_token)`) by the Head of Trading to resume trading.