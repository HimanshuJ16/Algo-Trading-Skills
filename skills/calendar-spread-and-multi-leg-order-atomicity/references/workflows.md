# Workflows for Multi-Leg Atomicity

0. **Native Combo Check**: Before legging anything, confirm the venue does not list the strategy as a tradable instrument. CME Globex, Eurex and most listed-derivatives venues publish calendar spreads and other combinations as their own instruments, matched by an implied-order engine that removes legging risk at the exchange. Leg manually only when no such instrument exists.
1. **Leg Sizing Validation**: Mathematically ensure the ratio of Leg 1 to Leg 2 strictly aligns with the intended exposure (e.g., 1:1 for a calendar spread, delta-weighted for an options delta-neutral spread). Reject non-positive ratios and prices before routing.
2. **Liquidity Assessment**: Identify the "Anchor Leg" (lowest average daily volume, or widest bid/ask spread in bps where volume data is unavailable) and the "Hedging Leg" (highly liquid proxy).
3. **Execution Routing**:
   - Route `Anchor Leg` as a standard passive Limit Order, once and only once.
   - Hold `Hedging Leg` in memory.
4. **Fill Processing**: On receipt of a `FILLED` or `PARTIALLY_FILLED` execution report for the Anchor Leg, immediately calculate the Hedging Leg quantity required for *that fill quantity* and route it.
5. **Hedging Route**: Route the `Hedging Leg` using an IOC (Immediate Or Cancel) or Aggressive Limit order, priced at the far edge of the slippage tolerance.
6. **Terminal-State Assessment**: Accumulate hedge fills as they arrive — a single IOC can produce multiple execution reports, and FIX treats `Canceled` as a legitimate terminal state for a partially executed IOC. Assess the hedge only on the order's terminal execution report, comparing cumulative hedge quantity against cumulative anchor quantity with a float tolerance.
7. **Reconciliation**: If the terminal report leaves an unhedged quantity — including the zero-fill case, which produces no fill reports whatsoever — trigger the broken-spread escalation to invoke the firm's emergency hedge protocol, and cancel the resting anchor order so the naked position stops growing.
8. **Execution Quality Review**: Compare the realised net spread (anchor VWAP minus hedge VWAP) against the target net spread and record the difference for TCA.
