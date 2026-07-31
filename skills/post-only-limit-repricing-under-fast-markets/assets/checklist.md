# Pre-Flight Checklist

- [ ] Are spread-crossing Post-Only limit orders detected before submission?
- [ ] Is passive repricing computed matching best bid/ask boundaries?
- [ ] Are consecutive reprice attempts capped ($\le 3$) to prevent rate limit throttling?
- [ ] Is tick size alignment enforced on repriced orders?