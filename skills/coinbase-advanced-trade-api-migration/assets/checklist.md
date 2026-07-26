# Pre-Flight Checklist

- [ ] Are legacy order fields mapped into the nested `order_configuration` schema?
- [ ] Is `side` formatted in uppercase (`BUY`/`SELL`)?
- [ ] Are numeric parameters (`limit_price`, `base_size`) converted to strings?
- [ ] Are response payloads from `/api/v3/brokerage/orders` properly parsed and normalized?
