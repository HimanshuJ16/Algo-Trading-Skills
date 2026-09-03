# TWSE Pre-Trade Validation and Order Routing Workflow

The procedure the reference implementation follows, in order. Each step names
the failure it prevents. Rule sources are in `standards.md`.

## 0. Assemble reference data for the trading day

Before any order is constructed, resolve per security:

| Item | Why it cannot be inferred |
|---|---|
| Security class (`EQUITY` / `ETF_REIT` / `ETN` / `WARRANT`) | Selects the tick schedule, odd-lot eligibility and the price-limit multiple. Codes overlap: `00679B` and `00400A` are ETFs, `2330` is a stock, six-digit codes may be warrants. |
| Auction reference price (開盤競價基準) | Equals the previous close only in the ordinary case. See `standards.md`. |
| Price-limit status | Standard 10%, `10 × multiple` for a domestic leveraged/inverse fund, or exempt (first five sessions of a new listing, foreign-component and offshore ETFs, secondary-listed foreign stocks). |
| Trading unit | 1,000 by default; **not** 1,000 for foreign-stock secondary listings and offshore ETFs. |
| Presence on today's 平盤下得融(借)券賣出 list, with its suspension flags | Changes daily. Required before any short sale can be priced below the reference. |

Refresh the short-sale list every trading day. A cached list will eventually
authorise a short into a security that lost margin eligibility overnight or
whose previous session closed limit-down.

## 1. Session and connectivity

Orders reach TWSE through a member securities firm, not through a TWSE API.
Automated order reports may be entered from 30 minutes before the session
opens. Establish which session the order is destined for — the constraints
below differ by session, and the session is not derivable from the payload.

## 2. Identity

Reject an order with no TWSE Investor ID on either the order or the engine.
Never fall back to a built-in identifier: an order stamped with a fabricated
registration ID is worse than one rejected for lacking a real one, because it
reaches the market wearing someone else's registration.

## 3. Ticket type against side

TWSE order entry carries 買賣別 (buy/sell) and 委託書種類 (cash / 融資 / 融券 /
借券) as separate fields. Reject the combinations that cannot exist:

- `MARGIN_SHORT` or `SBL_SHORT` with `side != SELL`.
- `MARGIN_LONG` with `side != BUY`.

Collapsing both fields into a single `side="SHORT_SELL"` value, as naive
implementations do, loses the distinction between a margin short and an SBL
short — which matters, because the two carry separate suspension flags on the
daily list.

## 4. Session constraints

**Odd-lot sessions** (盤中零股 and 盤後零股):

1. Reject warrants and ETNs outright — they may not trade odd lot.
2. Reject any margin or SBL ticket — odd-lot trading is cash only. An odd-lot
   short sale is invalid on its face, not an order awaiting a borrow.
3. Require 1 ≤ quantity ≤ 999.

**Regular sessions**: require a positive multiple of the trading unit. Do not
force a foreign-stock secondary listing or an offshore ETF through the 1,000
default; pass their actual unit.

**Order type and duration**: market, IOC and FOK exist only in the continuous
session (09:00–13:25). The opening and closing call auctions and both odd-lot
sessions accept limit-ROD alone, and TWSE returns anything else. Check this
*before* any price arithmetic — an IOC order at 13:26 is rejected for its
duration, not for its price.

## 5. Market-order carve-outs

A market order carries no price at all. Reject it where:

- the security has no daily price limit — with no band, a market order can
  print anywhere, which is exactly why TWSE refuses them there; or
- it is a margin or SBL short of a security restricted from below-reference
  pricing — TWSE bars the market order rather than trying to police where it
  prints.

A price supplied alongside a market order is a caller bug (limit and market
orders are not interconvertible on TWSE), so raise rather than silently
discard it.

## 6. Tick alignment — Article 62

Take the tick from the **order price's** band under the **instrument's**
schedule. Bands are lower-inclusive and upper-exclusive: a price exactly on a
boundary takes the coarser tick above.

Compare with `Decimal` modulo. A float tolerance is a tolerance in NT$ against
a grid whose coarsest step is NT$5, and it is a binary comparison against a
decimal lattice.

On rejection, return both neighbouring grid points so the caller can reprice.
Suppress a neighbour that falls below NT$0.01 — it is not quotable.

## 7. Daily price limit — Article 63 read with Article 62

```
amount = reference × pct / 100
if amount < 0.01: amount = 0.01          # 未滿一分者，以一分計
limit_up   = snap_down_to_tick(reference + amount)
limit_down = snap_up_to_tick(reference − amount)
if limit_down < 0.01: limit_down = 0.01  # 價格以跌至一分為限
```

Both bounds are snapped **toward** the reference, because the outward tick
breaches the percentage. Both are inclusive: an order exactly at the limit-up
price is the limit-up order and is tradeable.

Snapping can carry a bound across a band boundary — 11.11 × 0.9 = 9.999
rounds up on the NT$0.01 tick to 10.00, which sits in the NT$0.05 band. Every
TWSE band boundary is itself a multiple of both adjacent ticks, so re-resolving
the band once settles it; the implementation loops to a fixed point rather than
assuming that.

Skip the band entirely for a price-limit-exempt security — but do **not** skip
the reference price itself if the order is a short sale (step 8 still needs it).

## 8. 平盤以下 short-sale restriction

For a `MARGIN_SHORT` or `SBL_SHORT` ticket, reject when
`price < reference_price` unless the security is on today's list and unflagged.
Pricing exactly *at* the reference is always allowed — the comparison is
strictly-below, and getting it wrong by one tick rejects a legal order at 平盤
or admits an illegal one below it.

This check runs for exempt securities too. "No price band" does not mean "no
平盤".

## 9. Report

Return a structured verdict carrying the applied tick, both band bounds, and —
on a tick rejection — the nearest legal prices. A rejection a caller can act on
is worth more than a boolean. Echo the client order ID so the report can be
reconciled against the order log.

Distinguish the two failure modes throughout: malformed input raises
`ValueError`; only an exchange rule produces a non-accepted report. A caller
bug must never be indistinguishable from a rule rejection.

## 10. What this workflow does not cover

Intraday price stabilisation (瞬間價格穩定措施) and its delayed matching,
delayed open/close, disposition and altered-trading-method securities and their
prefunding, aggregate short-sale balance caps, position and settlement
management, and TPEx. Each is a separate control; none of them changes the tick
or the band computed above.
