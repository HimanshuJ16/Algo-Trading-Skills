# Pre-Flight Checklist

## Configuration
- [ ] Does every signalling `strategy_id` have an explicit allocation weight ($w_k > 0$)? (No fallback weight exists — an unweighted strategy must fail the pass.)
- [ ] Is `estimated_transaction_cost_bps` a **one-way all-in** cost, using the *half*-spread if derived from a quoted spread?
- [ ] Is $\epsilon_{\text{deadband}}$ calibrated for this instrument, and is the caller feeding the previous pass's consensus into `current_consensus_signal`?

## Correctness gates
- [ ] Does `target_notional_usd` carry an exposure **change** rather than an absolute position target?
- [ ] Is every signal in a batch for the arbitrated symbol only?
- [ ] Is internal order netting applied prior to order routing?
- [ ] Does the consumer branch on `status` rather than on `net_executable_notional_usd == 0.0`?

## Risk controls
- [ ] Does a risk-off veto override alpha regardless of the vetoing strategy's weight?
- [ ] Does a veto report a flat consensus ($0.0$), never a maximum short ($-1.0$)?
- [ ] Is `ValueError` treated as "do not trade this symbol" — never retried, never swallowed in favour of the sub-strategies' raw un-netted orders?

## Regulatory & reporting
- [ ] Is venue-level self-match prevention configured downstream, and are market-access pre-trade risk controls in place? (This layer enforces neither.)
- [ ] Are the firm's self-trade policies and procedures documented, given that arbitrated sub-strategies are *related* algorithms under shared control? (FINRA Rule 5210.02; CME Rule 534 advisory.)
- [ ] Are fills allocated back to contributing sub-strategies so per-strategy P&L survives netting?
- [ ] Does the TCA tally sum `internal_netting_savings_usd` only from passes that actually routed an order?
