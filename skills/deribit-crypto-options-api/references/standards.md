# Standards for Deribit Crypto Options API

| Metric | Engineering Standard |
|---|---|
| Inverse Premium Rule | All Deribit option premiums MUST be converted to USD using $P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index}}$. |
| Protocol Standard | All Deribit API calls MUST adhere to JSON-RPC 2.0 specs over WebSocket or HTTP. |
| Margin Buffer Ceiling | Initial margin for new option orders MUST NOT exceed $80\%$ of available equity. |
