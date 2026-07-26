# Workflows for Coinbase Advanced Trade API Migration

1. **Legacy Request Capture**:
   - Capture legacy order parameters: `product_id="BTC-USD"`, `side="buy"`, `type="limit"`, `price="50000"`, `size="0.1"`, `post_only=True`.
2. **Adapter Transformation**:
   - Convert `side` to uppercase `BUY`.
   - Build `order_configuration`:
     ```json
     {
       "client_order_id": "<uuid>",
       "product_id": "BTC-USD",
       "side": "BUY",
       "order_configuration": {
         "limit_limit_gtc": {
           "base_size": "0.1",
           "limit_price": "50000",
           "post_only": true
         }
       }
     }
     ```
3. **HTTP Dispatch**:
   - Send HTTP POST to `https://api.coinbase.com/api/v3/brokerage/orders`.
4. **Response Parsing & Normalization**:
   - Parse response: `{"success": true, "order_id": "...", "success_response": {...}}`.
   - Return standardized result to trading engine.
