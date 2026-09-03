# Broker & Framework Coverage — order-book-depth-processing-l2-l3

The depth-feed contract is **not** uniform across venues. Side encoding, quantity
semantics and even whether an order-by-order view exists differ, and a processor written
against one venue's conventions fails silently against another's — an unrecognised side
token routes a bid onto the offer side without raising.

| Feed / venue | Book granularity | Side encoding | Quantity semantics | Notes |
|---|---|---|---|---|
| Nasdaq TotalView-ITCH 5.0 | L3 (market-by-order), keyed on Order Reference Number | `B` / `S` in the Buy/Sell Indicator | Add carries total shares; `E`/`C`/`X` are **deductions**; `D` removes the remainder; `U` replaces under a **new** reference number | Reference numbers are day-unique — a repeat means a missed message. Prices are `Price (4)`: integers with four implied decimals |
| CME Globex MDP 3.0 (Simple Binary Encoding) | L2 market-by-price and, for supported instruments, market-by-order | Enumerated aggressor/entry side in the SBE message | Incremental refresh messages carry level actions (new / change / delete) against an explicit price level | MDP 3.0 is the exclusive Globex feed; it is **SBE, not ITCH** — the two share no layout |
| Coinbase Exchange `level3` / `full` | L3 (order-by-order) | `"buy"` / `"sell"`, lower case | `change` sets an absolute size — "The `size` property is the updated size at the price level, not a delta"; `match` is a fill; `done` removes | `level3` is the compact encoding of the same events as `full` |
| Coinbase Advanced Trade `level2` | L2 only | separate bid/ask arrays, no side token | `new_quantity` of `"0"` removes the level | No order-by-order view on this API |
| Binance Spot Diff. Depth Stream (`@depth`) | L2 | separate `b` / `a` arrays, no side token | **Absolute** level quantity — "If the quantity is zero, remove the price level from the order book" | A negative quantity is never emitted; receiving one means corruption |

Sources (retrieved 2026-08-27):

- Nasdaq TotalView-ITCH 5.0 specification — see `nasdaq-totalview-itch-feed-parsing`,
  which carries the full message-layout citations rather than duplicating them here.
- CME MDP 3.0 / SBE — CME Group Client Systems Wiki, *CME MDP 3.0 Market Data* and
  *MDP 3.0 — Market By Order Limited Depth Book Processing*,
  <https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/CME+MDP+3.0+Market+Data>
- Coinbase Exchange WebSocket channels (`full`, `level3`) — Coinbase Developer
  Documentation, <https://docs.cdp.coinbase.com/exchange/websocket-feed/channels>
- Binance spot — *How to manage a local order book correctly*,
  <https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md>

## Metric Definitions

Both metrics below are standard market-microstructure definitions, not venue rules.

- **Volume-weighted mid-price** (also *weighted midpoint*, *imbalance-based mid*):
  $P_{\text{wmid}} = \frac{V_{\text{ask}} P_{\text{bid}} + V_{\text{bid}} P_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}$,
  equivalently $w P_{\text{ask}} + (1-w) P_{\text{bid}}$ with
  $w = V_{\text{bid}} / (V_{\text{bid}} + V_{\text{ask}})$. The bid price carries the
  **ask** volume, so a bid-heavy touch prices above the arithmetic mid.

  This is **not** Stoikov's micro-price. The micro-price is a martingale estimate of the
  fair value conditional on the book, derived from the book's dynamics; the weighted mid
  is one of the biased estimators it is defined against. Using the two names
  interchangeably is common and inaccurate — see Stoikov, *The Micro-Price: A High
  Frequency Estimator of Future Prices* (SSRN 2970694).

- **Depth imbalance ratio**:
  $I = (V_{\text{bid}} - V_{\text{ask}}) / (V_{\text{bid}} + V_{\text{ask}}) \in [-1, 1]$,
  computed here over the top $N$ levels per side rather than the touch alone. The level
  count is a parameter of the signal, not an implementation detail — report it alongside
  the ratio.

## Regulatory & Operational Notes

No regulator surveyed here prescribes how a local order book is maintained; the rules
above are venue API contracts and standard quantitative definitions, not regulatory
requirements.

The one adjacent rule worth stating precisely, because it is easy to misapply: in the
United States, **17 CFR § 242.610(e)** (Regulation NMS, *Access to quotations*) requires
each national securities exchange and national securities association to establish rules
requiring its members "reasonably to avoid ... displaying quotations that lock or cross
any protected quotation in an NMS stock." Two qualifications matter here:

1. It governs **venue quote display**, not a subscriber's locally reconstructed book. It
   creates no obligation discharged by this skill.
2. It does not make a crossed consolidated quote impossible — "reasonably to avoid" is not
   a prohibition on the resulting market state, and non-NMS instruments, non-US venues and
   auction call phases are outside it entirely.

This provision was formerly numbered Rule 610(d) and was renumbered by later amendments
to § 242.610; cite the current paragraph. On 2026-06-11 the SEC **proposed** rescinding
Rule 611 and Rule 610(e); as of 2026-08-27 that is a proposal, not a final rule. Confirm
the current status before relying on it.

- 17 CFR § 242.610, <https://www.law.cornell.edu/cfr/text/17/242.610>
- SEC proposal to rescind Rules 611 and 610(e), 2026-06-11 (Federal Register,
  *The Trade-Through Rule and Locked and Crossed Markets Provisions of Regulation NMS*).

Operationally, the more common cause of a crossed *local* book is neither the market nor
the rulebook but a lost message or an unsynchronised mutation — which is what this skill's
guard is actually for.
