# Deep Workflow Reference — broker-account-margin-call-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Margin Utilization Calculation:**
   - Ingest `net_liquidation_value` (NLV), `maintenance_margin`, and — equally important —
     `excess_liquidity` and `available_funds` from the broker.
   - **Validate every value is finite first.** Every comparison against NaN evaluates
     False, so a NaN ratio walks past the breach, critical and warning tests and lands in
     the healthy branch. Unusable input must raise, not grade.
   - Calculate Maintenance Margin Utilization Ratio: $M_{\text{ratio}} = \frac{\text{Maintenance Margin}}{\text{Net Liquidation Value}}$.
   - **Do not floor NLV to avoid dividing by zero.** At NLV $\le 0$ the ratio is undefined;
     represent it as infinity and branch to a distinct `HALT_AND_ESCALATE` action. Compute
     the deficit from the *true* NLV — computing it from a floored value understates it by
     exactly the amount of negative equity.
   - **Cross-check the broker's cushion.** `excess_liquidity` is Equity with Loan Value
     minus maintenance margin, and ELV is not NLV. When it is negative the account is in
     deficiency and may be liquidated *even if* $M_{\text{ratio}}$ still looks healthy.
     Escalate to BREACH on that signal alone.

2. **Predictive New Order Veto Guard:**
   - Intercept outbound order requests with
     `guard_new_order(snapshot, margin_impact, initial_margin_impact=...)`.
   - **Gate on initial margin first.** New positions are opened against initial margin
     (Reg T: 50% on a long margin equity purchase), not maintenance margin (FINRA 4210:
     25% minimum). Veto when `initial_margin_impact > available_funds` — `available_funds`
     is the broker's own "room for new positions" figure (ELV − initial margin).
   - Then the maintenance projection: `projected_maint_margin = maintenance_margin + margin_impact`.
     If the projected ratio $\ge 0.85$, veto.
   - Source both impacts from the broker rather than estimating. At IBKR, submit the order
     with `Order.whatIf = true` and read `initMarginChange` / `maintMarginChange` off the
     `OrderState` returned to `openOrder`.
   - Risk-reducing orders may bypass the gates, but **verify they reduce risk**: an order
     flagged as de-leveraging with a positive margin impact is a contradiction and must be
     refused, not exempted.

3. **Evaluate Multi-Tier Risk Gates:**
   - **NORMAL** ($M_{\text{ratio}} < 0.85$): Proceed with strategy execution.
   - **WARNING** ($0.85 \le M_{\text{ratio}} < 0.95$): Block new position entries. Issue operator alert.
   - **CRITICAL** ($0.95 \le M_{\text{ratio}} < 1.0$): Cancel all open pending limit/stop orders to release reserved initial margin.
   - **BREACH** ($M_{\text{ratio}} \ge 1.0$): Trigger automated de-leveraging.

4. **Systematic De-Leveraging Plan Generation:**
   - Calculate `required_reduction = (maintenance_margin - target_margin) * buffer_multiplier`. Target margin is usually $75\%$ of NLV.
   - Score existing positions using a multi-factor model:
     - *Tail Risk*: Short options receive maximum priority (+1000).
     - *Margin Density*: Maintenance margin requirement divided by notional value (+100 * density).
     - *Liquidity*: High ADV assets receive higher priority, as they can be exited with minimal slippage (+ADV scaled).
   - Cap the liquidation units per position to a percentage of its Average Daily Volume (e.g. 10%) to prevent localized flash crashes.
   - **Check whether the capped plan actually clears the deficit.** Under illiquidity it
     often will not, and that is the intended trade-off — but an unmet reduction must
     escalate rather than be assumed handled.
   - **Re-price the plan under Portfolio Margin or SPAN before acting.** The scoring model
     assumes margin is separable per position (`reduction = units × margin per unit`).
     Portfolio-level regimes margin the stressed loss of the whole book, so unwinding one
     leg of a hedge can *increase* the requirement. Treat the output as a candidate
     ordering and validate each slice through the broker's own pre-trade check.
   - Generate execution slices to send to the execution algorithms (e.g., TWAP/VWAP).

## Failure Modes Observed in Production

- **Passive Waiting for Broker Liquidation:** Allowing broker RMS to liquidate positions at market, incurring massive slippage.
- **Uncanceled Pending Orders:** Leaving active limit orders in the market that consume margin during adverse price moves.
- **Illiquidity Liquidation Trap:** Dumping an illiquid micro-cap stock to cover margin, causing the stock to drop 20%, which further drops NLV, triggering a secondary margin call. ADV caps solve this.
- **Selling the Winners:** Naively selling highly liquid, low-margin-density winners first, leaving the portfolio concentrated in high-risk, illiquid losers.
- **Healthy Ratio, Liquidating Broker:** Grading only on `maintenance_margin / NLV` while
  the broker's `excess_liquidity` (computed off Equity with Loan Value) is already
  negative. The account reads NORMAL and keeps accepting orders while positions are being
  liquidated.
- **Silent NaN:** A dropped or corrupted account feed yields NaN, every threshold test
  returns False, and the engine reports a healthy account through the entire drawdown.
- **Trusted Bypass Flag:** An order tagged as de-leveraging is exempted from the gates
  without anyone checking that it actually reduces margin.
- **Waiting For a Window That Does Not Exist:** Designing the response around acting at
  BREACH when the broker liquidates in real time without notice. IBKR states positions may
  be liquidated without the account ever showing a margin warning.
- **Clock-Driven Square-Off:** Ignoring scheduled broker square-offs (Zerodha closes
  intraday MIS positions around 15:20 IST) because the margin ratio looks fine.

## Production Implementation Reference

- Reference code: `scripts/margin_call_engine.py` (`BrokerMarginCallEngine`, `AccountMarginSnapshot`).
- Automated unit tests: `scripts/test_margin_call_engine.py`.
