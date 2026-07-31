---
name: implementation-shortfall-minimization
description: >-
  Quantitative execution engine implementing Almgren-Chriss optimal trajectory planning and Perold (1988) Implementation Shortfall cost decomposition (Impact, Opportunity, Explicit Fees) in basis points.
domain: Execution Algorithms
subdomain: Optimal Execution & Transaction Cost Analysis (TCA)
tags: ["implementation-shortfall", "almgren-chriss", "perold-tca", "transaction-cost-analysis", "market-impact", "opportunity-cost", "basis-points"]
brokers_frameworks: ["Almgren-Chriss (2000)", "Perold (1988) TCA", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing institutional execution algorithms (IS Algos), evaluating transaction cost analysis (TCA), and optimizing trade trajectories for large block orders. Implementation Shortfall (IS) measures the complete economic cost of an order compared to the initial **Decision Price ($P_0$)**. This module uses the **Almgren-Chriss (2000)** optimal execution model to balance market impact against price volatility risk ($\lambda$), decomposing executed fills into Market Impact, Opportunity Cost, and Explicit Fees.

## Prerequisites

- Order parameters (`side`, `total_quantity`, `decision_price_p0`, `risk_aversion_lambda`, `time_horizon_intervals`).
- Executed trade fills list (`q_k`, `P_k`, `fee_k`) and final benchmark price ($P_{\text{final}}$).

## Workflow

1. **Almgren-Chriss Trajectory Generation**:
   - Calculate optimal trading trajectory $x_k$ across $N$ intervals using risk aversion $\lambda$:
     - High $\lambda \implies$ Front-loaded aggressive schedule to minimize price drift risk.
     - Low $\lambda \implies$ Linear TWAP schedule to minimize market impact.
2. **Execution Fill Ingestion & Benchmark Comparison**:
   - Ingest executed trade fills $q_k$ at price $P_k$ against Decision Price $P_0$.
3. **Perold (1988) IS Cost Decomposition**:
   - **Market Impact Slippage**: $\sum q_k (P_k - P_0)$ (for Buy).
   - **Opportunity Cost**: $(Q_{\text{total}} - Q_{\text{filled}})(P_{\text{final}} - P_0)$ (for Buy).
   - **Explicit Fees**: Commissions + exchange fees.
   - **Total IS bps**:
     $$\text{IS}_{\text{bps}} = \frac{\text{IS}_{\text{total}}}{Q_{\text{total}} \times P_0} \times 10,000$$
4. **Audit Report Generation**: Output structured `ImplementationShortfallReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Opportunity Cost of Unfilled Shares**: Evaluating execution quality solely on filled shares, ignoring adverse price movements on unexecuted quantities.
- **Using Arrival Price Instead of Decision Price**: Benchmarking IS against the price when the algo started instead of the Portfolio Manager's decision time ($P_0$), missing delay costs.
- **Fixed Trajectory in High Volatility**: Running a fixed TWAP trajectory during high volatility spikes instead of accelerating execution via Almgren-Chriss IS logic.

## Verification

- Instantiate `ImplementationShortfallEngine`. Input Buy 10,000 shares @ Decision Price $P_0 = \$100.00$. Execute 8,000 shares @ avg $P_k = \$100.25$, leaving 2,000 shares unfilled at $P_{\text{final}} = \$101.00$ (Fees $=\$20.00$). Verify engine calculates Impact Cost $=\$2,000$, Opportunity Cost $=\$2,000$, Explicit Fees $=\$20$, Total IS $=\$4,020$ ($40.20\text{ bps}$).
- Run `python scripts/test_implementation_shortfall_minimization.py`.

## Related Skills

- `execution-slippage-attribution-timing-vs-sizing`
- `post-trade-execution-quality-scorecard`
---
