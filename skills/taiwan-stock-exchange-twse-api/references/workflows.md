# TWSE Execution & Order Routing Workflow

1. **Authentication & Session Init**: Connect to broker DMA/TWSE gateway and supply registered FINI credentials.
2. **Pre-Trade Compliance Checks**:
   - Verify FINI ID.
   - Enforce 1,000 share lot multiples for standard board lot trading.
   - Calculate tick size ($0.01$ if price $< 50$, $0.05$ if price $\ge 50$) and round price.
   - Validate 10% daily price limit threshold against previous session close.
   - Check borrow locate status for `SHORT_SELL` orders.
3. **Execution & Allocation**: Route order to TWSE continuous trading book and record execution confirmations.
