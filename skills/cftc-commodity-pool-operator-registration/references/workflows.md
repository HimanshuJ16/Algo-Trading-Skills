# Workflows for CFTC CPO Compliance Monitoring

1. **Pre-Trade Hook Integration**:
   - The Order Management System (OMS) must hook into the `CftcCpoComplianceEngine` *before* submitting any order for a "commodity interest" (futures, swaps).
   - Equities and cash bonds do not count toward the numerator of these tests, but they contribute to the denominator (liquidation value).
2. **Live NAV Calculation**:
   - The engine must receive a real-time (or near real-time) feed of the fund's Liquidation Value, which equals: `Cash + Market Value of Securities + Unrealized PnL of all open positions`.
3. **Threshold Margin Calculation**:
   - Aggregate the Initial Margin of all currently open commodity interests.
   - Add the Initial Margin requirement of the *proposed* trade.
   - Check if this sum $\le 0.05 \times \text{Liquidation Value}$.
4. **Threshold Notional Calculation**:
   - Aggregate the absolute notional value of all currently open commodity interests.
   - Add the absolute notional value of the *proposed* trade.
   - Check if this sum $\le 1.0 \times \text{Liquidation Value}$.
5. **Decision Logic**:
   - If either Test 1 OR Test 2 is satisfied, the trade is legally compliant under the de minimis exemption. Proceed.
   - If BOTH tests fail, block the trade to prevent the fund from operating as an unregistered CPO.