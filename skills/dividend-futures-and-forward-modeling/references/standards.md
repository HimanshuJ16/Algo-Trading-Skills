# Standards for Dividend Futures and Forward Modeling

| Metric | Engineering Standard |
|---|---|
| Discrete Dividend Model | Single stock options and forwards MUST use discrete dividend present value ($\text{PV}(D)$). |
| Arbitrage Tolerance Band | Forward mis-pricing spread $\Delta_{\text{arb}}$ MUST exceed round-trip transaction costs before triggering arbitrage trades. |
| Expiry Boundary Guard | Dividends occurring after contract expiration ($t_i > T$) MUST be excluded from forward calculations. |