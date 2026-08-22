# Standards for Conditional Order Logic

| Metric | Engineering Standard |
|---|---|
| Native Trigger Precedence | If the venue or broker supports the trigger natively, use it. A broker-resident conditional order survives the client process, the client's feed and the client's host; a simulated one does not. Simulate only conditions the venue/broker cannot express. |
| Trigger Price Type | Every price condition MUST declare which price it triggers on — last trade, best bid, best offer, or mid. This is the same choice FIX exposes as `TriggerPriceType(1107)`; leaving it implicit means the trigger fires at a different moment than intended. |
| Single-Fire Guarantee | A triggered conditional order MUST fire exactly once. Evaluation and the `DORMANT` → `TRIGGERED` transition MUST occur under a single lock, so concurrent tick delivery cannot release two child orders. |
| Undecided ≠ False | Conditions MUST distinguish TRUE / FALSE / UNKNOWN. Missing, non-numeric, non-finite or stale inputs are UNKNOWN. Only a definite TRUE fires. Collapsing UNKNOWN to FALSE at the leaves makes `NOT(missing data)` fire a live order. |
| Stale Input Handling | Where staleness enforcement is enabled, a quote whose timestamp is absent or older than the configured `max_quote_age_seconds` MUST evaluate to UNKNOWN. The threshold is a per-deployment policy set from the feed's observed update cadence and the instrument's liquidity — there is no universal number; a 5 s bound suits a continuously quoted large-cap and is far too tight for an illiquid option series. A timestamp implausibly far in the *future* MUST also be treated as UNKNOWN: a millisecond timestamp parsed as seconds is never old and would defeat the check silently. |
| Operator Validation | Unsupported comparison operators MUST be rejected at construction. A silently-false operator produces a trigger that can never fire and raises no alarm. |
| Equality Comparison | Exact float equality MUST NOT be used as a trigger predicate. Equality requires an explicitly configured tolerance band. |
| Empty Composites | An empty `AND` gate is vacuously true (`all([]) is True`) and would fire on the first tick with nothing checked. Composite nodes MUST require at least one child. |
| Time Conditions | Target instants MUST be timezone-aware. All triggers evaluated within one tick MUST share one pinned evaluation clock. |
| OCO Semantics | When one leg of a one-cancels-the-other group fires, its still-dormant siblings MUST be cancelled before the next tick is processed, so both legs of a bracket cannot reach the venue. |
| Fire ≠ Fill | A fired trigger emits an order *intent*. It MUST still pass pre-trade risk control and can still be rejected by the venue. |

## Sources

- FIX 5.0 SP2, `<TriggeringInstruction>` component, `TriggerPriceType(1107)`: "the type of price that the trigger is compared to" — 1 = Best Offer, 2 = Last Trade, 3 = Best Bid, 4 = Best Bid or Last Trade, 5 = Best Offer or Last Trade, 6 = Best Mid. <https://www.onixs.biz/fix-dictionary/5.0.sp2/tagnum_1107.html>
- Interactive Brokers, *TWS API — Trigger Methods*: trigger method values 0 default, 1 double bid/ask, 2 last, 3 double last, 4 bid/ask, 7 last or bid/ask, 8 mid-point; "If a stop-variant is handled natively, the trigger method specified is ignored," and an incompatible trigger method means "the order may never trigger." <https://interactivebrokers.github.io/tws-api/trigger_method_limit.html>
- Interactive Brokers, *TWS API — Order Conditioning*: conditional orders support Price, Execution, Margin, Time, Volume and PercentChange conditions, chained with AND/OR conjunctions, with an optional flag under which "the active order will be cancelled if conditioning criteria is met." <https://interactivebrokers.github.io/tws-api/order_conditions.html>
- Interactive Brokers, *Conditional Orders* (Order Types and Algos): active orders remain active after exiting Trader Workstation and can be executed while not logged in — the basis for preferring broker-resident triggers over client-side simulation. <https://www.interactivebrokers.com/en/trading/orders/conditional.php>
- CME Group Client Systems Wiki, *Order Types for Futures and Options*: during the Continuous state a buy stop must be above and a sell stop below the last trade price; absent a last trade price the settlement price is used. Stop-with-protection converts to a limit at the stop price ± the product's protection points on trigger. <https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/Order+Types+for+Futures+and+Options>
- NYSE Client Notice, *Removal of Stop Orders and Good Till Cancelled Orders* (16 November 2015): NYSE and NYSE MKT stopped accepting new Stop and GTC orders from 26 February 2016, and existing GTC and Stop orders resting on the NYSE book were cancelled. <https://www.nyse.com/publicdocs/NYSE_Client_Notice_Removal_of_Stop_and_GTC_orders_112015.pdf>

## Not Verified

- No public, authoritative figure was found for a per-tick condition-tree evaluation latency budget. Any microsecond target quoted here would be invented. Measure your own evaluation cost against your feed's peak message rate and set the budget from that measurement; the engine's own cost is arithmetic over already-parsed values, and the dominant term in end-to-end trigger latency is the network path, not the tree.
