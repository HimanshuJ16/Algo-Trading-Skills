# Workflows for Futures Contract Roll Automation

1. **Liquidity & Expiration Audit**:
   - Audit front-month vs next-month trading volume, open interest, and days to expiration.
2. **Roll Signal Generation**:
   - Trigger roll signal on volume crossover ($V_{\text{next}} > V_{\text{front}}$) or days-to-expiration cutoff.
3. **Calendar Spread Construction**:
   - Calculate spread basis and construct atomic exchange calendar spread order.
4. **Order Execution & Audit Logging**:
   - Execute spread order and record roll audit log.