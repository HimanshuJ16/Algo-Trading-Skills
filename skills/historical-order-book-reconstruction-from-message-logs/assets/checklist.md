# Pre-Flight Checklist

- [ ] Are Level 3 messages (ADD, CANCEL, EXECUTE, REPLACE) ingested chronologically?
- [ ] Is active order ID map maintained in $O(1)$ hash map?
- [ ] Are L2 depth levels aggregated correctly across price points?
- [ ] Is crossed-book detection ($\text{Bid} \ge \text{Ask}$) active?
