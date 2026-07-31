# Workflows for Liquidity Seeking Across Lit and Dark Venues

1. **NBBO & Midpoint Calculation**:
   - Determine National Best Bid ($P_{\text{NBB}}$) and Offer ($P_{\text{NBO}}$) and compute midpoint $P_{\text{mid}}$.
2. **Dark Midpoint Sweep**:
   - Send IOC pings to dark ATS venues at $P_{\text{mid}}$ subject to minimum fill quantity limits.
3. **Lit Exchange Fallback**:
   - Route remaining unfilled quantity to Lit exchanges proportional to displayed depth.
4. **Audit Report Generation**:
   - Output structured liquidity seeking execution report.