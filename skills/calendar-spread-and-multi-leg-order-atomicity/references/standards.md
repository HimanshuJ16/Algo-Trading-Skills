# Standards for Multi-Leg Atomicity

| Metric | Rule |
|--------|------------|
| Native Combo Precedence | If the venue lists the strategy as a tradable instrument (CME Globex spreads, Eurex/Deribit combos), trade it natively. Exchange-matched combos remove legging risk; algorithmic legging only approximates it. |
| Anchor Leg Selection | The least liquid leg: lowest average daily volume, or the widest bid/ask spread in bps where volume data is unavailable. |
| Hedging Leg Order Type | IOC (Immediate or Cancel) or FOK (Fill or Kill). Never Market. |
| Slippage Tolerance | Maximum acceptable loss on the hedging leg must be explicitly defined (e.g. <= 5 bps) and applied in the direction that is *worse* for the side being traded: up for a BUY, down for a SELL. |
| Partial Fills | The hedging leg order size must dynamically adjust to match the *exact executed quantity* of the anchor leg, per fill. |
| Legging-Risk Trigger | Assessed on the hedge order's terminal execution report, never on a fill report alone. FIX permits an IOC to be partially executed and still terminate as `Canceled`, and a zero-fill IOC emits no fill report at all. |
| Break Response | Transition to a terminal BROKEN state, raise a critical alert to the emergency hedge protocol, and cancel the resting anchor order to cap naked exposure. |
| Quantity Comparison | Compare accumulated fill quantities with an explicit tolerance. Exact float equality manufactures phantom breaks on fractional-size venues. |

## Sources

- FIX Protocol `TimeInForce(59) = 3` (Immediate or Cancel): an IOC executes immediately in full or in part and cancels any remainder; FOK and IOC are the exceptions permitted to reach a `Canceled` terminal state after partial execution.
- CME Group, *Implied Orders* (CME Group Client Systems Wiki): Globex creates implied orders across spread and outright markets "without the risk to the trader/broker of being double filled or filled on one leg and not on the other leg." <https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/Implied+Orders>
- CME Group, *Spreads and Combinations Available on CME Globex*: calendar spreads are listed as exchange-traded instruments across supported products. <https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457089763>
