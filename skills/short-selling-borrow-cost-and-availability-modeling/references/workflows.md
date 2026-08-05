# Workflows for Short-Selling Borrow Cost and Availability Modeling

1. **Share Locate Pre-Check**:
   - Query share availability and utilization rate before placing short order.
2. **Borrow Fee Rate Calculation**:
   - Determine whether stock is General Collateral (GC) or Hard-To-Borrow (HTB).
3. **Holding Drag Accounting**:
   - Deduct daily borrow fee ($\text{Value} \times \frac{\text{Rate}}{365}$) from short strategy P&L.
4. **Squeeze & Recall Risk Audit**:
   - Monitor utilization spikes ($\ge 90\%$) for potential forced borrow recalls.
