# Standards for Decentralized Exchange (DEX) Integration Uniswap Style

| Metric | Engineering Standard |
|---|---|
| Constant Product Precision | Swap outputs MUST be calculated using exact constant product math ($x \cdot y = k$). |
| Maximum Slippage Cap | Algorithmic DEX swaps MUST NOT exceed $0.50\%$ max slippage tolerance. |
| MEV Private RPC Protection | High-value DEX swaps ($> \$10\text{k}$) MUST be routed via private RPC relays (Flashbots). |
