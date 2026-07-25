# Pre-Flight Checklist

- [ ] Does the exchange offer native COMBO/SPREAD orders? (If yes, use those instead of this algorithmic approach).
- [ ] Has the engine been tested against partial fills on the anchor leg?
- [ ] Is the hedging leg routed strictly as an IOC or Limit order with defined slippage bounds?
- [ ] Does the system alert a human trader or autonomous hedge engine if the spread breaks (legging risk realized)?