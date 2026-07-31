# Workflows for JSE South Africa Integration

1. **Alpha Code Validation**:
   - Verify 3-letter uppercase JSE alpha ticker (`NPN`).
2. **ZAC Cents Currency & Tick Audit**:
   - Audit order price in ZAC Cents and apply JSE tick size schedule.
3. **ZAR Notional Conversion**:
   - Calculate equivalent Rand (ZAR) notional value ($1\text{ ZAR} = 100\text{ ZAC}$).
4. **Audit Report Generation**:
   - Output structured JSE order report.
