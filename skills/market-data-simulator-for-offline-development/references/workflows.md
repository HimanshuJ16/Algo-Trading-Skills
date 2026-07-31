# Workflows for Market Data Simulation

1. **Simulation Initialization**:
   - Seed random generator and set GBM drift ($\mu$), volatility ($\sigma$), and spread parameters.
2. **GBM Price Path Generation**:
   - Compute log-normal price path steps.
3. **Order Book Spread & Volume Synthesis**:
   - Compute bid/ask prices and synthesize volume depth.
4. **Audit Report Generation**:
   - Output structured simulation report.
