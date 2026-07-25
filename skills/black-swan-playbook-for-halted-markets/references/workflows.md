# Black Swan Playbook Workflow

The following outlines the precise state-machine transitions and operational workflows required during a market halt event.

## Phase 1: Halt Detection & Lockdown
1. **Signal Ingestion:** Listen to exchange data feeds for `HALTED_LULD`, `HALTED_CIRCUIT_BREAKER`, or `NEWS_PENDING` flags.
2. **Halt Order Routing:**
   - Immediately suspend all new order generation for the affected symbol.
   - Issue `CANCEL` requests for all active working orders (Limit, Stop, Trailing).
3. **Risk Profile Adjustment:**
   - Alert the central risk engine to widen VaR bounds and dynamic stop-loss thresholds for the affected asset to account for fat-tail volatility.

## Phase 2: Defensive Proxy Hedging
1. **Position Assessment:** Determine the net open position delta for the halted asset. If zero, remain idle.
2. **Proxy Mapping:** Retrieve the predefined, highly liquid proxy instrument (e.g., SPY, QQQ) and its correlation Beta.
3. **Basis Risk Check:** Evaluate current market basis risk. If the divergence exceeds the safety threshold, abort hedging to prevent compounded losses.
4. **Execution:** Deploy a market or aggressive limit order in the proxy asset in the opposing direction. Size = `abs(OpenPosition * Beta)`.

## Phase 3: Auction Resumption
1. **Resume Signal Ingestion:** Detect the `RESUME_AUCTION` or `PRE_OPEN` state.
2. **Fair Value Calculation:** Run the internal pricing model to estimate the asset's current fair value, factoring in how the proxy asset moved during the halt duration.
3. **Auction Order Entry:** Submit an `AUCTION_RESUME_ORDER` (Limit) priced at the fair value estimate to liquidate or rebalance the position.
4. **Hedge Unwind:** Immediately submit orders to close the proxy hedge position, ensuring delta neutrality post-auction.

## Phase 4: Normalization
1. **Reconciliation:** Once the market officially un-halts and transitions to `NORMAL`, reconcile filled quantities from the auction.
2. **Resume Normal Trading:** Re-enable standard alpha-generating models and reset risk limits to normal regime levels.
