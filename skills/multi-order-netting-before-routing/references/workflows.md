# Workflows for Multi-Order Netting Before Routing

1. **Order Batch Aggregation**:
   - Aggregate internal buy and sell orders per symbol.
2. **Internal Mid-Price Crossing**:
   - Match opposing volume internally at $P_{\text{mid}} = (P_{\text{bid}} + P_{\text{ask}}) / 2$.
3. **External Residual Routing**:
   - Route net residual quantity to external market venue.
4. **Audit Report Generation**:
   - Output structured pre-routing netting report.
