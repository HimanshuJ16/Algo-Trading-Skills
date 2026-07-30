# Standards for Early Exercise Assignment Risk Management

| Metric | Engineering Standard |
|---|---|
| Ex-Dividend Assignment Rule | Short American calls MUST be closed/rolled prior to ex-div if $D_{\text{usd}} > \text{Extrinsic Value}$. |
| Extrinsic Value Minimum | Short ITM options with extrinsic value $< \$0.05$ MUST be flagged for assignment risk. |
| Expiration Style Classification | System MUST distinguish American-style (early exercisable) from European-style (cash-settled) contracts. |
