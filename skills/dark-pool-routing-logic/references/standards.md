# Standards for Dark Pool Routing Logic

| Metric | Engineering Standard |
|---|---|
| Toxicity Ceiling | Dark pools with post-trade adverse selection $> 5.0\text{ bps}$ MUST be excluded from routing. |
| MinQty Anti-Pinging | Child dark orders MUST enforce a Minimum Quantity ($\text{MinQty} \ge 200$ shares) to prevent HFT probing. |
| Midpoint Pricing | Dark pool child orders MUST specify Midpoint PEG or Passive limit pricing. |