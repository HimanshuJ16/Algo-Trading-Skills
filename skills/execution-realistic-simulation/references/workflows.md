# Deep Workflow Reference — execution-realistic-simulation

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Directional Spread Filling:**
   - Always fill BUY orders at (or above) the Ask price and SELL orders at (or below) the Bid price. Never fill at mid-price.

2. **Square-Root Market Impact Modeling:**
   - Model market impact using square-root participation law via `RealisticExecutionSimulator`:
     $$\text{Impact} = \frac{\text{Spread}}{2} + \gamma \times \sigma \times \sqrt{\frac{\text{Order Size}}{\text{ADV}}} \times P_{\text{mid}}$$

3. **Liquidity Depth & Partial Fill Simulation:**
   - Evaluate available order book depth at top levels. If $\text{Order Size} > \text{Depth}_{\text{available}}$, mark `is_partial_fill=True` and bound filled quantity to top-of-book depth.

4. **Complete Regulatory & Statutory Fee Stack:**
   - Calculate full statutory fee breakdown via `FeeBreakdown`: Brokerage, STT (0.0625% on sell options), Exchange Transaction Charges (0.05%), SEBI Turnover Fee, Stamp Duty (0.003%), and GST (18% on fees).

5. **Execution Latency Offset:**
   - Apply execution delay offset (e.g. 50ms – 200ms) between signal generation timestamp and simulated fill execution timestamp.

6. **Post-Trade Recalibration:**
   - Compare backtest modeled fill prices against actual paper/live broker execution fills; recalibrate impact coefficient $\gamma$ if systematic divergence occurs.

## Failure Modes Observed in Production

- **Mid-Price Fill Assumption:** Filling all simulated orders at mid-price, overstating backtest performance for illiquid options.
- **Flat Slippage Constant:** Using constant ₹1 slippage regardless of trade size relative to ADV.
- **Missing Regulatory Fees:** Omitting STT, GST, and Stamp Duty, understating costs for high-turnover strategies.
- **Instantaneous Execution:** Assuming zero latency between signal bar close and order fill.

## Production Implementation Reference

- Reference code: `scripts/fill_model.py` (`RealisticExecutionSimulator`, `SimulatedFillResult`, `FeeBreakdown`, `MarketType`).
- Automated unit tests: `scripts/test_fill_model.py`.
