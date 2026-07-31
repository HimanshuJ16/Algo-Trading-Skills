# Workflows for New Zealand Exchange (NZX) API

1. **Tick Size Compliance Audit**:
   - Verify order limit price matches NZX price step schedule ($NZD < 0.20 \implies 0.001$, $0.20-1.995 \implies 0.005$, $\ge 2.00 \implies 0.01$).
2. **FIX Message Construction**:
   - Format FIX 4.4 NewOrderSingle (`MsgType = 'D'`) with Tag 15 = NZD.
3. **Execution Handling**:
   - Parse ExecutionReport (`MsgType = '8'`) and track order status.
4. **Audit Report Generation**:
   - Output structured NZX order report.
