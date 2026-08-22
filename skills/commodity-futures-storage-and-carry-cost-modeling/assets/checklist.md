# Pre-Flight Checklist

- [ ] Are spot and futures prices synchronized to the exact same market timestamp?
- [ ] Is the spot quote the **contract-deliverable** grade at the **contract delivery point** (not a local or refiner assessment)?
- [ ] Is the commodity actually storable? (Electricity, weather and freight underlyings have no carry relation to model.)
- [ ] Is time to maturity $T$ an exact year fraction, on a **stated** day-count basis?
- [ ] Is the rate $r$ continuously compounded and on that **same** day-count basis as $T$?
- [ ] Are fixed per-unit storage charges (currency per unit per day/year) modelled additively as a present value, rather than folded into a percentage-of-spot rate?
- [ ] Are all inputs validated for **finiteness**, not just for sign? (NaN passes a `<= 0` check.)
- [ ] Is the full-carry price $(S_0 + U)e^{(r+c)T}$ computed and treated as an **upper bound**, not a two-sided fair value?
- [ ] Is `CASH_AND_CARRY` raised **only** when the futures price exceeds full carry net of round-trip costs?
- [ ] Is the cheap side reported as a *conditional* reverse-carry candidate rather than an arbitrage, given that the physical generally cannot be sold short?
- [ ] Does a negative implied convenience yield raise an inspection alert before any trade is sized?
- [ ] Are Contango, Backwardation **and Flat** ($F = S$) classified as three distinct states?
- [ ] Is implied-yield extraction flagged as unreliable inside roughly one day to expiry?
- [ ] Before routing, has any cash-and-carry signal been re-validated against real storage capacity, financing terms and delivery logistics?
