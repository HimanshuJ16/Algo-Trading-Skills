# Workflows for ESMA Double Volume Cap Mechanism

1. **Rolling Dark Volume Computation**:
   - Compute venue and EU-wide dark volume ratios over rolling 12-month window.
2. **ESMA Cap Breach Check**:
   - Compare ratios against 4.0% venue and 8.0% EU-wide thresholds.
3. **SOR Waiver Interception**:
   - Intercept non-LIS dark orders on suspended instruments.
4. **Order Rerouting**:
   - Redirect blocked dark orders to Lit venues or LIS waivers.