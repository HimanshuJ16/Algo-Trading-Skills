# Pre-Flight Checklist

- [ ] Does the exchange offer native COMBO/SPREAD orders for this strategy? (If yes, use those instead of this algorithmic approach — exchange matching gives real atomicity.)
- [ ] Are both legs simultaneously tradable (same session, neither halted, neither pre-open)?
- [ ] Are leg ratios and limit prices validated as positive before any order is routed?
- [ ] Is the anchor leg the *least liquid* leg, and is it routed exactly once (no duplicate submission on restart or retry)?
- [ ] Has the engine been tested against partial fills on the anchor leg?
- [ ] Is the hedging leg routed strictly as an IOC or Limit order with defined slippage bounds, applied in the direction adverse to the side traded?
- [ ] Does the broker adapter deliver hedge **terminal order states** (cancelled / expired / rejected), not just fills?
- [ ] Is legging risk assessed only on that terminal event — so multiple partial IOC reports do not fire a false break, and a zero-fill IOC does fire a real one?
- [ ] Are fill quantities compared with a float tolerance rather than exact equality?
- [ ] On a break, does the system cancel the resting anchor order AND alert a human trader or autonomous hedge engine?
- [ ] Is the realised net spread (anchor VWAP - hedge VWAP) recorded and compared against the target?
