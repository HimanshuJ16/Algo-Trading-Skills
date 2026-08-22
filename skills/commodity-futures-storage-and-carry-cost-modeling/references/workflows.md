# Workflows for Commodity Carry Cost Modeling

1. **Input Data Loading**:
   - Spot Price $S_0$ and Futures Market Price $F_{market}$, sampled at the **same timestamp** and for the **contract-deliverable** grade and delivery point.
   - Time to Expiration $T$ (years) on a stated day-count basis.
   - Annual Risk-Free Rate $r$, converted to continuous compounding on that same basis.
   - Storage cost: proportional rate $c$ (fraction of spot per year) and/or fixed charge $q$ (currency per unit per year).
   - Reject any non-finite input here. NaN satisfies `<= 0` checks and will otherwise reach `math.log` and produce a NaN price with a valid-looking regime label.

2. **Fixed Storage Present Value**:
   - $U_{PV} = q \cdot \frac{1 - e^{-rT}}{r}$, with the limit $U_{PV} = qT$ as $r \to 0$.
   - Skip when $q = 0$; the model then reduces to the pure proportional form.

3. **Full-Carry Price (no-arbitrage upper bound)**:
   - $F_{full} = (S_0 + U_{PV}) \cdot e^{(r + c) T}$.

4. **Implied Convenience Yield Calculation**:
   - $y = \frac{1}{T}\ln\left(\frac{F_{full}}{F_{market}}\right)$.
   - Warn when $T$ is below roughly one day: the $1/T$ factor turns tick noise into an implausible annualised yield.
   - Warn when $y < 0$: the upper bound is breached. Investigate quote synchronisation, deliverability and cost inputs first.

5. **Term Structure & Curve Slope Analysis**:
   - Basis: $\text{Basis} = S_0 - F_{market}$.
   - Market state: `CONTANGO` ($F > S$), `BACKWARDATION` ($F < S$), `FLAT` ($F = S$). Keep the equality case distinct rather than defaulting it into a regime.

6. **Arbitrage Threshold Audit**:
   - Cash-and-carry (enforceable): signal only if $F_{market} > F_{full} \cdot (1 + \text{round-trip cost})$. Execution is: buy spot, finance and store to $T$, sell the future, deliver into the contract.
   - Reverse carry (conditional, **not** an arbitrage): if $F_{market}$ is materially below the price implied by your baseline convenience yield view, record a candidate. Acting on it requires either existing inventory to sell or a genuine borrow/lease market for the physical. For most consumption commodities neither exists, and the "cheapness" is simply the convenience yield the market is paying.

7. **Escalation**:
   - Any cash-and-carry signal must be re-validated against live executable storage capacity, financing terms and delivery logistics before an order is routed. A theoretical bound breach that assumes free warehouse space is not a trade.
