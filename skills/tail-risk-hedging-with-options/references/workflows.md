# Workflows for Option Tail Risk Hedging

```mermaid
flowchart TD
    A[Portfolio AUM & Spot Inputs] --> B[Define Carry Budget Limit e.g. 2% AUM]
    B --> C[Select Target OTM Strike e.g. 15% OTM & 90 DTE]
    C --> D[Price Option via Black-Scholes Model]
    D --> E[Compute Whole Contract Allocation]
    E --> F[Simulate Payoff Under -20% and -30% Crash Regimes]
    F --> G[Deploy Option Overlay Position]
```

## Step-by-Step Execution

1. **Initialize `TailRiskHedger`**: Define annual carry budget percentage and contract sizing parameters.
2. **Run `plan_systematic_otm_put_hedge()`**: Input spot price, volatility, and portfolio valuation.
3. **Audit Payoff Coverage**: Ensure convex payout at -30% market drop offsets majority of portfolio losses.
4. **Monitor Carry Drag**: Track cumulative premium spend against annual performance benchmarks.
