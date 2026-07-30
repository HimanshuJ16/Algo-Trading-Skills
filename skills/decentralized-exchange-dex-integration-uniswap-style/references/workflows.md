# Workflows for Decentralized Exchange (DEX) Integration Uniswap Style

1. **Pool Reserve & Fee Parsing**:
   - Query pool reserves $x, y$ and fee rate $\gamma = 1 - \text{fee}$.
2. **Swap Output & Price Impact Calculation**:
   - $\Delta y = \frac{y \cdot \gamma \cdot \Delta x}{x + \gamma \cdot \Delta x}$.
   - $\text{Price Impact \%} = (1.0 - \frac{\Delta y / \Delta x}{y / x}) \times 100\%$.
3. **Slippage & Deadline Construction**:
   - Attach $\text{MinAmountOut} = \Delta y \times (1 - \text{Slippage})$ and $t_{\text{deadline}}$.
4. **Transaction Execution**:
   - Dispatch swap transaction to mempool or private RPC endpoint (Flashbots).
