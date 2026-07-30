# Pre-Flight Checklist

- [ ] Are dark pool venues scored by historical fill rate and post-trade markout toxicity?
- [ ] Are toxic dark pools ($\text{Toxicity} > \text{MaxToxicity}$) dynamically excluded?
- [ ] Is anti-pinging $\text{MinQty}$ enforced on all non-displayed child orders?
- [ ] Are child order sizes sliced proportionally to venue allocation scores?